"""Single token accounting used by scheduling and admission."""


class Budget:
    """Units: input tokens / 10 + output tokens (the conservative proxy meter)."""

    def __init__(self, total=float('inf')):
        self.total = total
        self.spent = 0.0

    def remaining(self):
        return self.total - self.spent

    def charge(self, input_tokens, output_tokens):
        self.spent += input_tokens / 10 + output_tokens
        return self.spent

    def admitted(self, reserve, output_limit):
        return self.remaining() >= reserve + output_limit
