"""Request-scoped provider accounting shared by planning and grounded analysis."""
from pesu_agent.llm.usage import LLMUsageTracker


class BudgetedProvider:
    def __init__(self, provider, max_calls=16, max_cost_usd=.05, should_stop=None):
        self.provider = provider
        self.provider_name = provider.provider_name
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd
        self.should_stop = should_stop
        self.reset()

    def reset(self):
        self.usage_tracker = LLMUsageTracker()
        self.attempt_count = 0

    def _reserve(self):
        if self.should_stop is not None and self.should_stop():
            raise RuntimeError("INVESTIGATION_CANCELLED")
        # Reported costs are checked before the next call; this is not a promise
        # that the last in-flight call cannot exceed the monetary threshold.
        if self.attempt_count >= self.max_calls or self.usage_tracker.total_cost_usd >= self.max_cost_usd:
            raise RuntimeError("LLM_BUDGET_REACHED")
        self.attempt_count += 1

    async def generate_structured(self, **kwargs):
        self._reserve()
        value, usage = await self.provider.generate_structured(**kwargs)
        self.usage_tracker.record_call(usage)
        return value, usage

    async def generate(self, **kwargs):
        self._reserve()
        result = await self.provider.generate(**kwargs)
        self.usage_tracker.record_call(result.usage)
        return result
