# deepseek_tokenizer source

- **Source**: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/tokenizer.json
- **Repo**: `deepseek-ai/DeepSeek-V4.1-Flash` (Hugging Face), revision `2cba9e42aa026125f3ed06c6d98c1db82f7ca027`
- **Downloaded**: 2026-10-04
- **sha256**: `c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b`
- **Size**: 6,367,257 bytes
- **Vocab size**: 129,280

This file differs byte-for-byte from the official V3 (`621ac2e3…`), V3.1 / V3.2-Exp
(`32b34a41…`) tokenizer files, and from DeepSeek's `deepseek-recipe` V4.1 copy
(`81f64d12…`). The difference is only in the special-token table (this file adds
special token 129,280 and omits 128,000). **Body tokenization is identical** — the
same plain text encodes to the same number of tokens under every copy. Evidence and
the recomputed sample counts are recorded in `docs/specs/distill-capacity.md` §9.1
and C11.
