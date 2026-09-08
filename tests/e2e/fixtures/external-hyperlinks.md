# Document extraction evaluation

Evaluation date: 31 August 2026

## Objective

This anonymized evaluation checks how a generic document tool extracts current text and revision
information from an office document.

## Protocol

The source document contains:

- ordinary text;
- a passage associated with a review comment;
- deleted wording;
- replacement wording;
- revision metadata.

## Result

| Document element | Extraction result |
| --- | --- |
| Ordinary text | Extracted |
| Review comment | Not extracted |
| Replacement wording | Extracted |
| Deleted wording | Not extracted |

## References

- [Project documentation](https://documentation.example.invalid/document-tool)
- [Package index](http://packages.example.invalid/document-tool)

## Reproduction outline

```python
def extract_current_text(document: bytes) -> str:
    return convert_to_markdown(document)
```
