# AI Model Routing

## Goals
Select the safest adequate model while balancing privacy, quality, cost, latency and availability.

## Inputs
- project privacy classification;
- data sensitivity;
- internet/offline status;
- task type;
- required capabilities;
- context size;
- complexity;
- latency target;
- user/project budget;
- provider health.

## Hard rule
If content is local-only, no protected content or sensitive derived representation may be transmitted to a cloud model.

## Routing tiers
1. Local lightweight — classification, simple commands, short transformations.
2. Local capable — private coding/requirements/repository work and offline fallback.
3. Cost-efficient cloud — permitted general/coding work.
4. Premium reasoning/coding — complex architecture/debugging/review when policy and budget allow.

## Provider abstraction
Define provider-neutral chat/reasoning/embedding interfaces. Support model capability metadata and health/cost information. Do not encode vendor names in domain logic.

## Budget controls
Per job/project/user: maximum estimated cost, tokens, premium calls and runtime. Escalation beyond configured limits requires user approval or a lower-cost fallback.
