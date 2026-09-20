class Usage:
    def __init__(self, prompt=0, completion=0, total=0, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.prompt_tokens_details = {"cached_tokens": cached} if cached else None
