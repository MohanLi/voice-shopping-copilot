# Prompts

Every prompt the system sends to a model lives in this folder as a plain file, is loaded at run time by
`voiceshop.agents.prompts`, and is logged (name + version + hash) with each call. Nothing is hidden in code.

The table mapping each prompt to its LangGraph node, agent or tool is filled in as the agents are built
(see [PROGRESS.md](../PROGRESS.md)).
