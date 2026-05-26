# ADR-0014: Column-Level Field Encryption

**Status:** Accepted

## Context

Fintech target users have a recurring requirement that plaintext PII (SSN, customer identifiers, account numbers) must not land in the warehouse. The warehouse is treated as the asset of concern: warehouse admins, BI consumers, query logs, and exfiltration paths all see whatever Filedge writes. Source files in the Watched Directory are treated as a controlled landing zone and are not part of this threat boundary.

Today, Filedge's `Transform` is purely declarative type coercion ("no business logic — that belongs in the application layer consuming the destination"). That left operators with two unsatisfactory choices: encrypt upstream in a Fetcher (which is not always controllable, e.g. when the producer is a vendor export or an off-the-shelf sync tool), or load plaintext and re-process with warehouse-native primitives (which violates the "no plaintext in warehouse" policy for the window between load and re-write). A first-class, in-pipeline encryption capability is needed.

Several architectural questions had to be settled before any code lands:

1. **Threat model**: what is encryption actually protecting against?
2. **Key ownership**: does Filedge integrate with KMS, or stay out of key management?
3. **Algorithm**: randomized confidentiality, deterministic for joinability, or both?
4. **Joinability**: how do operators handle the legitimate need to join/dedup on a protected column without compromising the confidentiality guarantee?
5. **Concept placement**: extend `Transform`, or introduce a distinct concept?

## Decision

Filedge gains a **Field Encryption** capability: per-column declarative encryption and keyed hashing applied between Transform and Connector write, configured inline in `pipeline.yaml` alongside the existing column mapping.

### Threat model: plaintext PII must not land in the warehouse

The encryption boundary is the warehouse write. Plaintext in the Watched Directory is acceptable and out of scope for this feature; operators who require source-side encryption are expected to solve that upstream in their Fetcher or sync layer. Filedge's guarantee is narrow and explicit: **no row written by a Connector contains plaintext for any column declared under `encrypt:`**.

### Key ownership: Filedge does crypto, not KMS

Filedge does not integrate with AWS KMS, GCP KMS, Vault, Azure Key Vault, or any other key-management service. The operator supplies key material at runtime via environment variables or a secrets mount, following the same convention used for Connector credentials. Filedge knows how to encrypt and HMAC with the keys it is given; it does not know how to fetch, unwrap, rotate, or revoke them.

Operators retain envelope encryption as a pattern above Filedge: wrap a data encryption key (DEK) with a KMS-managed key encryption key (KEK) outside Filedge, persist the wrapped DEK with operational metadata, unwrap at Filedge startup, and supply the raw DEK via env. KEK rotation happens independently of Filedge.

### Algorithm: AES-256-GCM only, randomized

Filedge ships exactly one encryption algorithm at v1: **AES-256-GCM with a fresh random 12-byte nonce per encryption**. The output is framed as `version || nonce || tag || ciphertext` and base64-encoded into a destination column of type `string`. Same plaintext encrypted twice produces different ciphertext.

Deterministic encryption (AES-256-SIV) is explicitly deferred. The use case it addresses — joinable ciphertext — is served by a separate, cleaner primitive (HMAC, below) without exposing a decryption path on the joinable form.

### Joinability: HMAC, not deterministic encryption

When an operator needs the destination to support joins or dedup on a protected column, the pattern is to declare **two destination columns** from the same source: one ciphertext column under `encrypt:`, and one HMAC token column under `hash:`. The HMAC token is one-way, uses a separate key, and reveals equality only.

Filedge ships exactly one hash algorithm at v1: **HMAC-SHA256**, base64-encoded into a destination column of type `string`.

This separation has two properties that matter:

- **One-way by construction.** A leaked HMAC key plus a constrained domain (SSNs, phone numbers) enables a rainbow attack but does not produce a decryption oracle for unrelated columns. A leaked deterministic-encryption key decrypts everything immediately. Smaller blast radius.
- **Visible in YAML.** A `hash:` block is an explicit declaration that the operator is exposing equality on this column on purpose. A deterministic-encryption choice looks like a routine algorithm choice but has the same leakage property — easier to misread in security review.

### YAML shape

```yaml
columns:
  - source: ssn
    dest: ssn_ct
    type: string
    encrypt:
      algorithm: aes-256-gcm
      key: env:DATA_KEY
  - source: ssn
    dest: ssn_join
    type: string
    hash:
      algorithm: hmac-sha256
      key: env:JOIN_KEY
```

- `encrypt:` and `hash:` are independent column attributes. A column may have neither, one, or both.
- When `encrypt:` is present, the column `type:` must be `string` (validation rejects other types at YAML load time).
- The same source column may appear in multiple column entries with different `dest:` names — the standard way to produce both ciphertext and HMAC tokens from one source value.
- Keys are referenced by env-var name (`env:NAME`) or secrets-mount path. Key material never appears in YAML.

### Library and implementation

The implementation uses the `cryptography` Python library (OpenSSL-backed, AES-NI accelerated). Pure-Python crypto and PyCryptodome are rejected in code review.

The cipher and HMAC contexts are instantiated once per File at the start of Streaming Load and reused across rows. No parallel-row processing is introduced for encryption; performance is acceptable under the existing sequential model because AES-NI puts the crypto well below the existing I/O ceiling.

### Failure semantics

A crypto error (key length wrong, malformed input that the cipher rejects, library exception) fails the row, which fails the File, per Strict Mode. Crypto errors are deterministic — they do not benefit from retry — so this is consistent with the existing model.

### Audit story

- The Audit DB stores no key material and no plaintext.
- Row-level provenance (`_source_file_hash`, `_ingested_at`) is unchanged: the destination row that contains ciphertext still carries the originating Content Hash.
- The Audit Export shows encrypted columns as opaque strings; readability outside the warehouse is by design no longer possible without keys.

## Consequences

- **Filedge becomes a system that handles PII.** Target users will treat it as in-scope for SOC2 / PCI / GDPR controls they apply to PII-touching systems. This is a one-way door.
- **Operator owns key management end-to-end.** Filedge will not help with rotation, escrow, or KMS migration. The operator's existing security tooling is the authority on keys. This is a deliberate scope cut; it is the reason Filedge does not gain a KMS provider matrix.
- **`Transform` stays pure.** Type coercion is unchanged; Field Encryption is introduced as a distinct concept in `CONTEXT.md` to preserve the existing Transform contract.
- **One source column commonly maps to multiple destination columns.** The 1:1 column mental model is no longer universal. Pipeline configs for sensitive fields will be visibly busier in YAML and in destination DDL.
- **Encrypted columns are not analyzable in the warehouse.** Aggregations, filters, and GROUP BY on `encrypt:`-declared columns produce no meaningful result. Operators who need analytics must either use the `hash:` companion column or decrypt outside the warehouse.
- **HMAC over a small domain is grindable.** HMAC of a 9-digit SSN with a known key is exhaustible. The `hash:` mechanism reduces the blast radius of a warehouse compromise but does not eliminate it under an attacker who also has the HMAC key.
- **No automatic rotation.** Re-encrypting historical data under a new DEK is an operator-side migration: re-ingest the source File with the new key, or write a one-off rotation job that reads the warehouse, decrypts, re-encrypts. Filedge does not provide tooling for this at v1.
- **`filedge inspect` is unchanged.** Schema inference operates on the raw source File, not the destination shape. Encryption is invisible to inspect.
- **Future expansion is constrained, not blocked.** If a real customer requires native KMS integration, AES-SIV, tokenization with a vault, or automatic rotation, each can be added as a separate concern on top of the v1 surface without invalidating it.

## Alternatives Considered

**Warehouse-native encryption (BigQuery AEAD, Snowflake masking, TDE).** Load plaintext, then re-write encrypted in the warehouse. Rejected because it requires plaintext to land in the warehouse, even briefly, which violates the policy the feature exists to enforce. Warehouse-native primitives remain useful for analyses where the policy permits warehouse-side decryption, but they cannot replace ingest-time encryption.

**Fetcher-side encryption.** Have the upstream producer encrypt before writing to the Watched Directory. Recommended where possible — Filedge does not duplicate this — but rejected as the sole solution because the producer is often not controllable (vendor exports, off-the-shelf sync tools, customer-deposited files).

**Filedge integrates with KMS directly.** Add a `kms:` block in `pipeline.yaml` with an AWS/GCP/Vault/Azure registry, wrap/unwrap DEKs at runtime. Rejected at v1 because it commits Filedge to a multi-provider maintenance matrix, introduces a new failure mode (KMS unreachable, IAM denied, key disabled), and overlaps with infrastructure target users already operate. Reachable later as an opt-in registry without invalidating the v1 contract.

**Deterministic encryption (AES-256-SIV) for joinable columns.** Use deterministic encryption on columns that need to be joined. Rejected because (a) it conflates two security properties (confidentiality and joinability) into a single key, (b) a key leak immediately decrypts all historical data — strictly worse blast radius than HMAC, and (c) HMAC is the better-known industry pattern for de-identified joinability.

**Tokenization with a separate vault.** Replace values with opaque tokens; a vault holds the mapping. Rejected as a different category of feature — it requires a stateful vault, vault availability, vault audit story, and reverse-tokenization tooling. May revisit if a customer specifically requires PCI scope reduction.

**Per-row parallelism to make crypto fast.** Process rows in a worker pool to overlap encryption with I/O. Rejected because (a) crypto is not the bottleneck under AES-NI, (b) row parallelism is a broad architectural change that affects every pipeline component, not a crypto concern, and (c) the obvious sequential implementation already performs well below the existing I/O ceiling.
