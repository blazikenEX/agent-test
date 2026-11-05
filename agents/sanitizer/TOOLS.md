```markdown
# Tools: Hybrid Security Filters
Tool files can be found inside 'lib' directory.

## Local System Filters
- **regex_filters.py**: Runs local regex checks to instantly swap out obvious emails, SSNs, and phone numbers before the LLM processing stage.
- **canary_tokens.py**: Injects unique, randomized strings into the processing thread. If the LLM echoes these tokens back in an unsafe context, the system flags a data leak.
```
