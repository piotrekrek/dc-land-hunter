"""Keep the plan ogólny fact of the gmina current: refetch the register's act list, then redo the verdicts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from sqlalchemy import text

from dclh_pipeline.config import load_profiles, load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.planning import gmina_plan_fact
from dclh_pipeline.profiles import features_from_detail, profile_verdicts
from dclh_pipeline.scoring.run import to_jsonable
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.ru import RuActRegisterConnector, load_plan_register

REPROFILE_PAGE = 2000  # sites per page


def reprofile_sites() -> int:
    """Recompute the profile verdicts of every stored site from its stored features (no new search).

    The gmina's plan ogólny status comes from the latest act list. It goes into the verdict only: the stored
    features keep what the search saw. Returns the number of sites.
    """
    cfg = load_profiles()
    register = load_plan_register(SnapshotStore(get_settings().raw_dir), load_sources().get("ru_acts"))
    done, last = 0, ""
    # In pages by site_id: all 100k+ sites' features at once do not fit in a worker's memory.
    while True:
        with session_scope() as session:
            rows = session.execute(
                text(
                    "SELECT site_id, detail -> 'features', parcel_ids[1] FROM sites WHERE site_id > :last "
                    "ORDER BY site_id LIMIT :n"
                ),
                {"last": last, "n": REPROFILE_PAGE},
            ).all()
            for site_id, stored, parcel_id in rows:
                features = features_from_detail(stored)
                if register is not None:
                    features = replace(features, gmina_plan_ogolny=gmina_plan_fact(parcel_id or "", register))
                verdicts = profile_verdicts(features, cfg)
                session.execute(
                    text(
                        "UPDATE sites SET profiles = CAST(:p AS jsonb), verdict_built = :b, "
                        "verdict_container = :c WHERE site_id = :id"
                    ),
                    {
                        "p": json.dumps(to_jsonable(verdicts), ensure_ascii=False),
                        "b": verdicts["built"].category.value,
                        "c": verdicts["container"].category.value,
                        "id": site_id,
                    },
                )
        if not rows:
            break
        done, last = done + len(rows), rows[-1][0]
    return done


def refresh_plan_register() -> dict[str, Any]:
    """Fetch the act list again (about 10 minutes), keep it as the fallback once it parses, redo the verdicts.

    A failed or incomplete fetch raises before anything is written, so the verdicts keep the last good list.
    """
    settings = get_settings()
    config = load_sources()
    spec = config.get("ru_acts")
    store = SnapshotStore(settings.raw_dir)
    connector = RuActRegisterConnector(spec)
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:
        snapshot = connector.fetch(client, store)
    acts = connector.parse(snapshot)
    store.mark_good(snapshot)
    return {"acts": len(acts), "sites": reprofile_sites()}
