"""Which pictures render together, and when each request should be done (docs/pillars/generation.md, "Batching"
and "Estimates").

`plan_batches` is the one rule both the worker and the estimate use: take the oldest picture still to render,
then every later picture of the same model, in queue order, up to the batch size. A model's pictures from
several requests share a fresh ComfyUI process; a request never waits behind a later one of its own model.

A batch costs a restart, a fixed overhead (loading the model) and a time per picture, learnt from the batches
measured: a straight line through (pictures, render seconds) once two batch sizes are known, the configured
overhead and the mean per picture before that, the configured figures before any batch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cost:
    restart: float
    overhead: float
    image: float

    def batch(self, size: int) -> float:
        return self.restart + self.overhead + self.image * size


def plan_batches(images: list[dict], max_batch: int) -> list[list[dict]]:
    remaining = list(images)
    batches = []
    while remaining:
        model = remaining[0]["settings"]["model"]
        batch = [image for image in remaining if image["settings"]["model"] == model][:max(1, max_batch)]
        taken = {id(image) for image in batch}
        remaining = [image for image in remaining if id(image) not in taken]
        batches.append(batch)
    return batches


def costs(measured: list[dict], defaults: dict, limit: int = 20) -> tuple[float, dict[str, Cost]]:
    """(the restart estimate, each measured model's cost); a model not in the dict costs `default_cost`."""
    restarts = [row["restart_seconds"] for row in measured if row.get("restart_seconds") is not None][:limit]
    restart = sum(restarts) / len(restarts) if restarts else float(defaults["restart"])
    by_model: dict[str, list[tuple[int, float]]] = {}
    for row in measured:
        points = by_model.setdefault(row["model"], [])
        if len(points) < limit:
            points.append((int(row["size"]), float(row["render_seconds"])))
    result = {}
    for model, points in by_model.items():
        overhead, per_image = float(defaults["batch_overhead"]), float(defaults["image"])
        sizes = {size for size, _ in points}
        mean_size = sum(size for size, _ in points) / len(points)
        mean_time = sum(seconds for _, seconds in points) / len(points)
        if len(sizes) >= 2:
            spread = sum((size - mean_size) ** 2 for size, _ in points)
            per_image = sum((size - mean_size) * (seconds - mean_time) for size, seconds in points) / spread
            per_image = max(1.0, per_image)
            overhead = max(0.0, mean_time - per_image * mean_size)
        else:
            per_image = max(1.0, (mean_time - overhead) / mean_size)
        result[model] = Cost(restart, overhead, per_image)
    return restart, result


def default_cost(restart: float, defaults: dict) -> Cost:
    return Cost(restart, float(defaults["batch_overhead"]), float(defaults["image"]))


def estimate(pending: list[dict], max_batch: int, cost_of, *, running: dict | None = None) -> dict[str, float]:
    """Seconds from now until each request with a picture running or still to render should be done.
    `running` is the batch in progress: {"model", "size", "elapsed", "request_ids"}; `cost_of(model)` a Cost."""
    done_at: dict[str, float] = {}
    clock = 0.0
    if running:
        expected = cost_of(running["model"]).batch(running["size"])
        clock = max(5.0, expected - running["elapsed"])
        for request_id in running["request_ids"]:
            done_at[request_id] = clock
    for batch in plan_batches(pending, max_batch):
        clock += cost_of(batch[0]["settings"]["model"]).batch(len(batch))
        for image in batch:
            done_at[image["request_id"]] = clock
    return done_at
