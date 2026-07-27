"""Provider-independent LLM interface, one real adapter, and a
deterministic fake for tests.

Nothing outside this package imports a vendor SDK — see `base.py` for
the `LLMProvider` interface every adapter implements, `factory.py` for
how the configured provider is constructed from `Settings`,
`anthropic_provider.py` for the one real implementation, and
`fake_provider.py` for the deterministic test double every test in this
codebase uses instead of a real network call.
"""
