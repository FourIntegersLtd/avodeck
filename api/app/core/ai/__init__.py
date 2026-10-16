"""The AI kit (architecture 6): the only way any slice calls a model, OCR or speech service.

`kit.AiKit` runs a task: identifiers out first (`pii`), the cell's in-region model (`cell`), pack
context as data, structured output checked against the task's schema, an `ai_calls` row with
metadata only. `provenance` checks every extracted value against its cited OCR words. The real
Bedrock, Azure Document Intelligence and Azure Speech clients arrive with the cloud cell; until then
`Recorded*` stand-ins replay committed recordings behind the same interfaces.
"""
