"""AI Chat API routes for real-time coaching conversations."""

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from typing import List, Optional
import json
import logging
from datetime import datetime

from app.schemas import CoachingPlanResponse, ReflectionCreate
from app.services.storage import get_storage, InMemoryStorage
from app.services.ai_service import get_ai_service, AIService
from app.utils.validators import sanitize_input, validate_reflection_content, validate_habit_id

logger = logging.getLogger(__name__)

router = APIRouter()


def get_storage_dep() -> InMemoryStorage:
    """Dependency for storage."""
    return get_storage()


def get_ai_service_dep() -> AIService:
    """Dependency for AI service."""
    return get_ai_service()


@router.post("/habits/{habit_id}/chat")
async def chat_with_coach(
    habit_id: str,
    request: Request,
    message: dict,
    storage: InMemoryStorage = Depends(get_storage_dep),
    ai_service: AIService = Depends(get_ai_service_dep)
):
    """
    Chat with AI behavioral coach.
    
    Expects: {"message": "user's message", "context": {"reflections_count": int, "last_plan": Optional[dict]}}
    Returns: Streaming JSON response with AI coaching
    """
    if not validate_habit_id(habit_id):
        raise HTTPException(status_code=400, detail="Invalid habit ID")
    
    habit = storage.get_habit(habit_id)
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    user_message = message.get("message", "").strip()
    context = message.get("context", {})
    
    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Sanitize input
    sanitized_message = sanitize_input(user_message)
    is_valid, error_msg = validate_reflection_content(sanitized_message)
    if not is_valid and len(sanitized_message) < 10:
        # Allow shorter messages for chat
        if len(sanitized_message) < 3:
            raise HTTPException(status_code=400, detail="Message too short")
    
    # Get recent reflections for context
    reflections = storage.get_reflections(habit_id, limit=5)
    coaching_plan = storage.get_coaching_plan(habit_id)
    
    # Build conversation history for AI
    system_prompt = f"""You are MindMirror AI, a compassionate behavioral coach specializing in helping users overcome {habit.name.replace('_', ' ')} addiction.

Your role:
- Listen empathetically to the user's struggles
- Ask insightful follow-up questions to understand triggers
- Provide actionable advice based on behavioral science
- Never shame or judge the user
- Focus on understanding WHY habits occur
- Suggest replacement habits when appropriate
- Help users recognize patterns in their behavior

Current Context:
- Habit: {habit.name}
- User has {len(reflections)} reflections
- Recent reflections: {', '.join([r.content[:50] for r in reflections[-3:]]) if reflections else 'None yet'}

Respond in a warm, conversational tone. Keep responses concise (2-4 sentences) unless the user asks for more detail.
Always end with a supportive question or encouragement."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": sanitized_message}
    ]
    
    # Add previous coaching plan context if available
    if coaching_plan:
        plan_context = f"""
        
Current Coaching Plan:
- Mission: {coaching_plan.daily_plan.get('mission', 'N/A')}
- Risk Level: {coaching_plan.risk_prediction.get('risk_level', 'unknown')}
- High-risk times: {', '.join(coaching_plan.risk_prediction.get('high_risk_times', []))}

Remember this context when responding."""
        messages[0]["content"] += plan_context
    
    try:
        # Stream the response
        async def generate_response():
            try:
                response_text = await ai_service.client.chat_completion(
                    messages=messages,
                    temperature=0.7,
                    max_tokens=500
                )
                
                if response_text:
                    # Clean up response
                    cleaned = response_text.strip()
                    if cleaned.startswith("```"):
                        cleaned = '\n'.join(cleaned.split('\n')[1:-1])
                    
                    # Send as SSE-like stream
                    yield f"data: {json.dumps({'type': 'message', 'content': cleaned})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                else:
                    fallback = "I'm here to support you. Could you tell me more about what you're experiencing right now?"
                    yield f"data: {json.dumps({'type': 'message', 'content': fallback})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    
            except Exception as e:
                logger.error(f"Error generating chat response: {e}")
                error_msg = "I apologize, but I'm having trouble connecting right now. Please try again in a moment."
                yield f"data: {json.dumps({'type': 'error', 'content': error_msg})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
        
        return StreamingResponse(
            generate_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
        
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process chat request")


@router.post("/habits/{habit_id}/quick-reflection")
async def quick_reflection(
    habit_id: str,
    reflection_data: ReflectionCreate,
    storage: InMemoryStorage = Depends(get_storage_dep),
    ai_service: AIService = Depends(get_ai_service_dep)
):
    """
    Submit a quick reflection and get immediate AI feedback.
    This combines reflection submission with instant coaching response.
    """
    if not validate_habit_id(habit_id):
        raise HTTPException(status_code=400, detail="Invalid habit ID")
    
    habit = storage.get_habit(habit_id)
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    # Sanitize and validate
    sanitized_content = sanitize_input(reflection_data.content)
    is_valid, error_message = validate_reflection_content(sanitized_content)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)
    
    # Add reflection
    reflection = storage.add_reflection(habit_id, sanitized_content)
    if not reflection:
        raise HTTPException(status_code=500, detail="Failed to add reflection")
    
    # Get all reflections for AI analysis
    all_reflections = storage.get_reflections(habit_id)
    
    # Generate coaching plan
    coaching_plan = await ai_service.analyze_reflections(habit.name, all_reflections)
    
    if coaching_plan:
        plan_dict = coaching_plan.model_dump()
        from app.models import CoachingPlan
        coaching_plan_obj = CoachingPlan(
            habit_id=habit_id,
            trigger_analysis=plan_dict["trigger_analysis"],
            risk_prediction=plan_dict["risk_prediction"],
            daily_plan=plan_dict["daily_plan"],
            replacement_habits=plan_dict["replacement_habits"],
            behavior_graph=plan_dict["behavior_graph"],
            motivation_message=plan_dict["motivation_message"],
            next_actions=plan_dict["next_actions"],
            weekly_summary=plan_dict.get("weekly_summary")
        )
        storage.save_coaching_plan(habit_id, coaching_plan_obj)
        
        # Return both reflection and coaching plan
        return {
            "reflection": {
                "id": reflection.id,
                "content": reflection.content,
                "created_at": reflection.created_at.isoformat()
            },
            "coaching_plan": coaching_plan,
            "message": "Reflection recorded! Here's your updated coaching plan."
        }
    
    return {
        "reflection": {
            "id": reflection.id,
            "content": reflection.content,
            "created_at": reflection.created_at.isoformat()
        },
        "message": "Reflection recorded successfully!"
    }


@router.get("/habits/{habit_id}/chat/suggestions")
async def get_chat_suggestions(
    habit_id: str,
    storage: InMemoryStorage = Depends(get_storage_dep)
):
    """Get suggested conversation starters based on user's progress."""
    if not validate_habit_id(habit_id):
        raise HTTPException(status_code=400, detail="Invalid habit ID")
    
    habit = storage.get_habit(habit_id)
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    
    reflections = storage.get_reflections(habit_id, limit=10)
    coaching_plan = storage.get_coaching_plan(habit_id)
    
    # Generate contextual suggestions
    suggestions = [
        "I'm feeling an urge right now...",
        "What should I do when I feel stressed?",
        "Help me understand my triggers better",
        "What's a good replacement habit for today?",
    ]
    
    # Add personalized suggestions based on data
    if coaching_plan:
        risk_level = coaching_plan.risk_prediction.get("risk_level", "medium")
        if risk_level == "high":
            suggestions.insert(0, "I'm struggling today, can you help?")
        
        high_risk_times = coaching_plan.risk_prediction.get("high_risk_times", [])
        if high_risk_times:
            suggestions.append(f"How do I handle {high_risk_times[0]} cravings?")
    
    if len(reflections) > 5:
        suggestions.append("What patterns have you noticed in my reflections?")
    
    return {"suggestions": suggestions[:5]}
