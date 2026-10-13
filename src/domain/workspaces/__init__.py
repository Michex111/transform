"""Workspace domain: durable workspaces, their runtimes, and executions.

The three concepts are deliberately separate (see ``docs/cloud-workspaces-plan.md``):

* a **workspace** is the durable logical resource — it outlives every runtime;
* a **runtime** is the disposable compute instance currently attached to it;
* an **execution** is one submitted unit of work, and it outlives both.
"""
