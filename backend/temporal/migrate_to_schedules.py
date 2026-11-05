"""One-time migration: DailyScheduledFeedingWorkflow -> Temporal Schedules.

For each running DailyScheduledFeedingWorkflow, this reads the schedule from
the workflow memo (written by the old PUT /schedule endpoint), creates the
equivalent Temporal Schedule, and terminates the old workflow.

    uv run python -m backend.temporal.migrate_to_schedules            # dry run
    uv run python -m backend.temporal.migrate_to_schedules --apply
"""

import argparse
import asyncio

from backend.temporal.client import get_temporal_client
from backend.temporal.schedules import upsert_schedule

LEGACY_QUERY = "WorkflowType = 'DailyScheduledFeedingWorkflow' AND ExecutionStatus = 'Running'"


async def migrate(apply: bool) -> None:
    client = await get_temporal_client()
    found = 0
    async for wf in client.list_workflows(LEGACY_QUERY):
        found += 1
        memo = await wf.memo()
        try:
            device_id = int(memo["device_id"])
            hour, minute = (int(p) for p in str(memo["schedule"]).split(":"))
            amount = int(memo["amount"])
        except (KeyError, ValueError) as e:
            print(f"SKIP {wf.id}: memo missing or malformed ({e!r}): {memo}")
            continue

        print(f"{wf.id}: device={device_id} {hour:02d}:{minute:02d} amount={amount}")
        if apply:
            await upsert_schedule(client, device_id, hour, minute, amount)
            await client.get_workflow_handle(wf.id, run_id=wf.run_id).terminate(
                "Migrated to Temporal Schedule"
            )
            print("  -> schedule created, legacy workflow terminated")

    if found == 0:
        print("No running DailyScheduledFeedingWorkflow executions found.")
    elif not apply:
        print("Dry run. Re-run with --apply to migrate.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="actually migrate")
    asyncio.run(migrate(parser.parse_args().apply))


if __name__ == "__main__":
    main()
