# Changelog

## 0.14.0

### Breaking changes

- **scripts:** score per advisor, hide labels and remember failed models (`b70e5c1`)
- **skills:** frame the question and deliver a five-part council verdict (`48ce085`)
- cast council models from the user's catalog, not hard-coded ones (`44defab`)
- **manifest:** declare the council tools, run file scopes and settings (`2ca8bdb`)
- **skills:** drive the council through plugin tools, not a script (`7aac450`)

### Features

- **scripts:** add the council helper with verbatim prompts and failover (`0b2b7f4`)
- **skills:** add the llm council orchestrator skill (`48d58bb`)
- **manifest:** register the llm council skill pack (`53b8145`)
- **subagents:** add the council member, chairman and clerk personas (`5b5fa55`)
- **scripts:** read seat chains from the personas and install them (`9bb3810`)
- **skills:** run council seats as persona sub-agents with native failover (`ce31a69`)
- **subagents:** add five thinking-lens advisors and a verdict chairman (`c8aaa24`)
- **skills:** cast seats per user, confirm once and reuse the cast (`e8f2d42`)
- **main:** run the council bookkeeping as agent tools (`cc9134d`)

### Documentation

- **readme:** describe the council, its cast, failover and credits (`31a57d1`)
- **readme:** explain the persona sub-agents, install and failover (`85ae8d9`)
- **readme:** describe the advisors, anonymity, failover and credits (`2c0812b`)
- **readme:** explain casting, exclusions and failover chains (`968c53b`)
- **readme:** document the tools, permissions, limits and tests (`51db9cb`)
- add the security review notes (`b796e81`)

### Tests

- add a fake pi-desktop host and a test entry point (`e370c42`)
- cover ranking, casting, prompts and run paths (`e624ac0`)
- cover the council tools from config to report (`847e27a`)
- cover persona install, removal and the manifest (`b236581`)

### CI

- print the changelog section of a version (`20ab4b6`)
- publish a github release when the manifest version changes (`9c6caa8`)

### Chores

- ignore build output, python caches and council runs (`3640f5c`)
- **manifest:** rename the plugin to eskwisit.llm-council (`52540ce`)
- **subagents:** remove the identical member personas (`011e6ed`)
- **manifest:** describe the five-advisor council (`7889119`)
- **manifest:** describe model casting and the files it keeps (`f9b0638`)
- license the plugin under apache-2.0 (`f0189a9`)
- stop ignoring python caches (`5257934`)
- **scripts:** remove the python helper (`3ce4c2a`)
- **subagents:** describe the council as modelled on karpathy's (`549e40f`)
