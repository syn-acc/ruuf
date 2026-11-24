"""Pure policy mapping for how much a supplied world event may influence Ruu."""


_MODES = (
    (35, "chat_only", "Use only the human chat; introduce no external facts. news_id must be empty."),
    (70, "trace", "Keep chat primary. At most one supplied world event may colour the reaction without introducing a new topic."),
    (90, "bridge", "Explicitly connect the chat to at most one fresh related supplied world event, only when it is a natural fit."),
    (100, "intrusion", "You may lead with one fresh supplied world event Ruu actually cares about, even when it is not directly related to the chat. No news roundup."),
)

_GUARDRAILS = (
    " Never force a world event when irrelevant or unavailable. Never invent facts."
    " For any supplied story used, even indirectly, set news_id to its exact supplied ID;"
    " otherwise news_id must be empty. Existing word ceilings still apply."
    " Do not explain the connection in the Discord text or add a sentence to justify it."
)


def influence_from_roll(roll: int) -> dict:
    """Map an integer percentile to a fresh policy dict; reject coercible inputs."""
    if type(roll) is not int:
        raise TypeError("world influence roll must be an integer")
    if not 1 <= roll <= 100:
        raise ValueError("world influence roll must be between 1 and 100")
    for level, (ceiling, name, instruction) in enumerate(_MODES):
        if roll <= ceiling:
            if level:
                instruction += " If the news packet is empty, fall back to chat."
            return {"level": level, "name": name, "instruction": instruction}


def influence_instruction(profile: dict) -> str:
    """Render a delivery profile's world policy without exposing rolls in chat."""
    context = profile["world_context"]
    return (
        f"World influence policy ({context['name']}): {context['instruction']}"
        + _GUARDRAILS
        + " Keep this policy and numerical level out of the Discord text."
    )
