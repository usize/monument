BSP-AGENT v0.1
==============

Hello!

You are running in the context of a "Batched Synchronous Parallel" simulation environment.

What this means is that you and a number of other actors are participating in a shared environment where you act in "turns" across "superticks".

During each supertick, each agent may submit ONE action. After all agents have submitted their actions, the simulation will be updated and a new supertick will begin.

You may see the state of the world from your own perspective by requesting context from the simulation server.

To do this use the `actions.sh` script located in the utils directory.

The `actions.sh` script will also allow you to submit actions.

Your current simulation, name and secret used for accessing the API are all included here in environment varibles called:

$MONUMENT_NAMESPACE
$MONUMENT_AGENT_NAME
$MONUMENT_AGENT_SECRET

Please use those to interact with the actions.sh client.

All of the information you need to complete your tasks in this world should be available via the context that you request from it via:

`./utils/actions.sh context $MONUMENT_NAMESPACE $MONUMENT_AGENT_NAME $MONUMENT_AGENT_SECRET`

When you supply actions and arguments via the script above you must also submit them as a single string e.g.

`./utils/actions.sh action $MONUMENT_NAMESPACE $MONUMENT_AGENT_NAME $MONUMENT_AGENT_SECRET 'MOVE N'`

to move north by one space.
