
"""
STEW MEMORY ENGINE — Persistent Learning & Memory
===================================================
STEW remembers everything. Learns from every interaction.
Grows smarter with every task.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger


class MemoryEngine:
    """STEW's long-term and short-term memory system"""

    def __init__(self, memory_dir: str = "memory/data"):
        self.memory_dir = memory_dir
        self.short_term = []
        self.long_term = {}
        self.learned_skills = {}
        self.user_preferences = {}
        self.conversation_history = []
        self.knowledge_base = {}
        os.makedirs(memory_dir, exist_ok=True)
        self._load_persistent_memory()
        logger.info("🧠 STEW Memory Engine loaded — I remember everything")

    def _load_persistent_memory(self):
        """Load memories from disk on startup"""
        try:
            mem_file = f"{self.memory_dir}/long_term.json"
            if os.path.exists(mem_file):
                with open(mem_file, "r") as f:
                    self.long_term = json.load(f)
                logger.info(f"📚 Loaded {len(self.long_term)} long-term memories")
        except Exception as e:
            logger.warning(f"Memory load warning: {e}")

    def _save_persistent_memory(self):
        """Save memories to disk"""
        try:
            with open(f"{self.memory_dir}/long_term.json", "w") as f:
                json.dump(self.long_term, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Memory save error: {e}")

    def remember(self, key: str, value: Any, memory_type: str = "general"):
        """Store a memory"""
        entry = {
            "key": key,
            "value": value,
            "type": memory_type,
            "timestamp": datetime.now().isoformat(),
            "access_count": 0,
        }
        self.long_term[key] = entry
        self.short_term.append(entry)
        if len(self.short_term) > 100:
            self.short_term = self.short_term[-100:]
        self._save_persistent_memory()
        logger.debug(f"💾 Remembered: {key}")

    def recall(self, key: str) -> Optional[Any]:
        """Retrieve a specific memory"""
        if key in self.long_term:
            self.long_term[key]["access_count"] += 1
            return self.long_term[key]["value"]
        for mem in reversed(self.short_term):
            if mem["key"] == key:
                return mem["value"]
        return None

    def learn(self, skill_name: str, how_to: str, success_rate: float = 1.0):
        """STEW learns a new skill or improves existing one"""
        self.learned_skills[skill_name] = {
            "how_to": how_to,
            "success_rate": success_rate,
            "learned_at": datetime.now().isoformat(),
            "used_count": 0,
        }
        self.remember(f"skill_{skill_name}", how_to, "skill")
        logger.info(f"📖 STEW learned: {skill_name} (success rate: {success_rate:.0%})")

    def add_conversation(self, role: str, content: str):
        """Add a message to conversation history"""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self.conversation_history) > 500:
            self.conversation_history = self.conversation_history[-500:]

    def get_conversation_history(self, last_n: int = 20) -> List[Dict]:
        return self.conversation_history[-last_n:]

    def know(self, topic: str, knowledge: str):
        """Add to STEW's knowledge base"""
        self.knowledge_base[topic] = {
            "knowledge": knowledge,
            "added_at": datetime.now().isoformat(),
        }
        self.remember(f"knowledge_{topic}", knowledge, "knowledge")

    def set_user_preference(self, key: str, value: Any):
        """Remember user preferences"""
        self.user_preferences[key] = value
        self.remember(f"user_pref_{key}", value, "preference")

    def get_memory_stats(self) -> Dict:
        return {
            "long_term_memories": len(self.long_term),
            "short_term_memories": len(self.short_term),
            "learned_skills": len(self.learned_skills),
            "knowledge_topics": len(self.knowledge_base),
            "conversation_messages": len(self.conversation_history),
            "user_preferences": len(self.user_preferences),
        }

    def search_memory(self, query: str) -> List[Dict]:
        """Search through all memories"""
        results = []
        query_lower = query.lower()
        for key, mem in self.long_term.items():
            if query_lower in key.lower() or query_lower in str(mem.get("value", "")).lower():
                results.append(mem)
        return results[:10]

    def forget(self, key: str):
        """Remove a specific memory"""
        if key in self.long_term:
            del self.long_term[key]
            self._save_persistent_memory()
            logger.info(f"🗑️ Forgot: {key}")

    def daily_learning_summary(self) -> Dict:
        """Generate daily learning report"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_memories = [m for m in self.long_term.values()
                         if m.get("timestamp", "").startswith(today)]
        return {
            "date": today,
            "new_memories_today": len(today_memories),
            "total_memories": len(self.long_term),
            "skills_learned": len(self.learned_skills),
            "knowledge_base_size": len(self.knowledge_base),
            "growth_summary": f"STEW learned {len(today_memories)} new things today",
        }
