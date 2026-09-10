# Agents
This file provides guidance when working with code in this repository. It is deliberately small:
repository conventions, the architecture skeleton, and how to build and test.

**Subsystem knowledge lives in Memco Shared Memory, not here.**

## Repository

This repository is the public home of the official Memco SDKs. Each language directory holds a
hand-written SDK wrapping a gRPC client generated from the service contract, published to that
language's package registry for developers who have no access to Memco's servers. The contract and
the generated clients come from the private server repository and are emptied and rewritten on each
export, so they are never edited here; everything else is hand-written. The SDK's job is what the
generated client does not do — resolving credentials and endpoints, verifying the connection,
presenting results as the language's own types, turning every failure into a typed error, and
surfacing what the service reports about its limits and deprecations. That boundary is the thing to
hold on to: anything a caller can observe is this repository's concern, and anything only the
service can decide stays the service's, because an SDK that hardcodes a value the service owns goes
stale and starts refusing work the service would have accepted.

## Your key considerations

The following are guidelines for how to write code that is both maintainable and easy to understand.
Always keep this in mind when writing code.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### 5. Always review your own code

Always review your own code using a fresh agent team focusing on the code quality, simplicity, clarity, and security.
Do this prior to informing your user that your changes are live.

### 6. Follow test-driven development (TDD)

When fixing a bug or adding a new feature, first write a test that reproduces the bug or specifies the desired behavior.
Run the test to confirm it fails for the right reason before changing production code. 100% code coverage is not required, but key behaviors must be covered.
