
import json
import hashlib
import time
from typing import Dict, Any, Optional, List
from collections import OrderedDict

from astrbot.api.star import Context
from astrbot.core.provider import Provider
from astrbot.api import logger  # Use AstrBot's logger from api

class IntentRouter:
    # 缓存配置
    CACHE_MAX_SIZE = 100
    CACHE_TTL_SECONDS = 300  # 5分钟过期
    
    def __init__(self, context: Context, config: Dict[str, Any]):
        self.context = context
        self.config = config
        self._cache: OrderedDict[str, tuple] = OrderedDict()  # key -> (result, timestamp)

    def _get_cache_key(self, text: str, contexts: List) -> str:
        """Generate cache key from text and recent context."""
        # 只使用最近2条上下文生成key，避免上下文变化导致缓存失效
        ctx_str = str(contexts[-2:]) if contexts else ""
        return hashlib.md5(f"{text}:{ctx_str}".encode()).hexdigest()
    
    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cached result if valid (not expired)."""
        if key not in self._cache:
            return None
        result, timestamp = self._cache[key]
        if time.time() - timestamp > self.CACHE_TTL_SECONDS:
            # 过期，删除
            del self._cache[key]
            return None
        # 移动到末尾 (LRU)
        self._cache.move_to_end(key)
        return result
    
    def _set_cached(self, key: str, result: Dict[str, Any]):
        """Store result in cache with LRU eviction."""
        self._cache[key] = (result, time.time())
        self._cache.move_to_end(key)
        # 清理超过上限的缓存
        while len(self._cache) > self.CACHE_MAX_SIZE:
            self._cache.popitem(last=False)

    async def analyze_intent(self, user_text: str, contexts: List[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """
        Analyze user intent using dynamically built system prompt.
        Now includes conversation context for better multi-turn judgment.
        Includes caching for performance optimization.
        """
        if contexts is None:
            contexts = []
        
        # --- 缓存检查 ---
        cache_key = self._get_cache_key(user_text, contexts)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Router cache hit for: {user_text[:30]}...")
            return cached
            
        router_config = self.config.get("router_config", {})
        provider_id = router_config.get("router_provider")
        router_model = router_config.get("router_model", "")
        
        # How many recent messages to include for context (configurable, default 4)
        context_limit = router_config.get("context_turns", 4)
        
        logger.debug(f"Router Config: Provider={provider_id}, Model={router_model}")

        if not provider_id:
             logger.warning("Router provider ID is empty.")
             return None
        
        provider = self.context.get_provider_by_id(provider_id)
        if not provider:
            logger.error(f"Router provider not found: {provider_id}")
            return None

        # --- Build Dynamic System Prompt from Frames (Fixed Slots) ---
        
        categories_map = {} # name -> list of descriptions
        
        # Helper to process fixed slots
        # Helper to process fixed slots
        def process_slots(tier_key, slot_count):
            tier_config = self.config.get(tier_key, {})
            if not tier_config:
                return

            for i in range(1, slot_count + 1):
                name = tier_config.get(f"r{i}_name", "").strip()
                desc = tier_config.get(f"r{i}_desc", "").strip()
                
                if name:
                    if name not in categories_map:
                        categories_map[name] = []
                    if desc and desc not in categories_map[name]:
                        categories_map[name].append(desc)

        # Collect from all tiers
        process_slots("tier_low", 6)
        process_slots("tier_mid", 6)
        process_slots("tier_high", 6)



        # Flatten descriptions
        final_cat_map = {}
        for k, v in categories_map.items():
            final_cat_map[k] = " / ".join(v)
            
        # Build prompt text
        cat_lines = []
        for name, desc in final_cat_map.items():
            cat_lines.append(f'- "{name}": {desc}')
        cat_section = "\n".join(cat_lines)
        valid_cats_json = str(list(final_cat_map.keys())).replace("'", '"')

        # Get Thresholds
        t_low = self.config.get("tier_low", {}).get("max_score", 3)
        t_mid = self.config.get("tier_mid", {}).get("max_score", 7)
        
        # --- Format conversation context ---
        context_section = ""
        if contexts and len(contexts) > 0:
            # context_turns is number of rounds (1 round = user + assistant), so multiply by 2 for messages
            message_limit = context_limit * 2
            recent_contexts = contexts[-message_limit:] if len(contexts) > message_limit else contexts
            context_lines = []
            for msg in recent_contexts:
                # Handle both dict format and string format
                if isinstance(msg, dict):
                    role = msg.get("role", "user")
                    content = msg.get("content", "")[:200]  # Truncate long messages
                elif isinstance(msg, str):
                    role = "unknown"
                    content = msg[:200]
                else:
                    continue  # Skip invalid format
                if content:
                    context_lines.append(f"[{role}]: {content}")
            if context_lines:
                context_section = "\n\nRecent Conversation Context:\n" + "\n".join(context_lines)
        
        # Check template
        template = router_config.get("router_manual_prompt", "")
        if template and "{categories}" in template:
             system_prompt = template.replace("{categories}", cat_section)
        elif template:
             system_prompt = template
        else:
             system_prompt = f"""You are a Model Router. Analyze user input and output JSON.

Output format:
{{"difficulty_score": 1-9, "category": String, "reasoning": "Brief"}}

Categories: {valid_cats_json}
{cat_section}

=== DIFFICULTY SCALE (1-9) ===

LOW TIER (1-{t_low}) - Lightweight Model:
- 1: Fixed replies (greetings, thanks, confirmations)
- 2: Single-step execution (weather query, translation, format conversion, single tool call)
- 3: Standard reasoning (algorithm implementation, math proofs, code analysis, concept explanation)

MID TIER ({t_low+1}-{t_mid}) - Standard Model:
- 4: Multi-step integration (search+analyze+suggest, 3000+ word document comprehension)
- 5: Complex generation (function refactoring, multi-file understanding, long-form writing)
- 6: Quality planning (project design, complex itinerary planning, architecture design)

HIGH TIER ({t_mid+1}-9) - Premium Model:
- 7: Very long context (10000+ lines code analysis, multi-document synthesis)
- 8: Cross-domain integration (interdisciplinary system design, complex technical decisions)
- 9: Extreme tasks (complete product architecture, frontier problem analysis, large-scale refactoring)

=== KEY RULES ===
1. Function/tool calls do NOT automatically add difficulty - only if multi-step coordination is needed
2. "Check weather" = 2, NOT 4-5 (simple tool call with clear parameters)
3. Standard reasoning tasks (proofs, algorithms) = 3, NOT higher
4. Score based on THINKING COMPLEXITY, not task type
5. Consider conversation context - "yes" might refer to a complex prior question
"""

        prompt = f"{context_section}\n\nCurrent User Input: {user_text}\n\nOutput JSON object."
        
        try:
            logger.debug("Sending request to Router Model...")
            response = await provider.text_chat(
                prompt=f"{system_prompt}\n\n{prompt}",
                contexts=[],
                model=router_model if router_model else None
            )
            
            raw_text = response.completion_text.strip()
            logger.debug(f"Router Raw Output: {raw_text}")
            
            # Basic cleanup
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                if lines[0].startswith("```"): lines = lines[1:]
                if lines[-1].startswith("```"): lines = lines[:-1]
                raw_text = "\n".join(lines)
            
            data = json.loads(raw_text)
            
            # --- 缓存结果 ---
            self._set_cached(cache_key, data)
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Router JSON Parse Error: {e}. Raw: {raw_text}")
            return None
        except Exception as e:
            logger.error(f"Router Unexpected Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

