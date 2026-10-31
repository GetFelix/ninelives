+++
title = "What if every machine decision could be replayed?"
description = "An idea being explored, not something built: models in the loop of a device fleet, with every decision traced to its inputs and model version, and replayed."
date = 2026-10-31
slug = "what-if-every-decision-could-be-replayed"

[taxonomies]
tags = ["inference", "replay", "design"]
+++

A gateway on a pipeline closes a valve at 02:14. A camera on a line flags a good part as bad. A depot reroutes a van around a road that was open. In the morning someone asks the same four questions every time: what did the machine see, which model was running, what did the model say, and would it do the same again?

Usually the answer is a log line someone chose to write, a model that has since been redeployed, and a guess. This post is about what it would take for the answer to be a set of records instead. It's an idea I'm exploring for NineLives, not something that exists. The longer version is a [proposal](https://github.com/GetFelix/ninelives/pull/37) that is still open for argument.

## What already works

NineLives runs workloads with [felixd](https://github.com/GetFelix/felixd), and part of this is already there. A felixd workload is a WebAssembly module, run under wasmtime, whose only inputs are records on Felix streams. It has no clock, no random numbers and no network, so given the same records it reaches the same state every time. When a machine is lost, another one claims the workload, restores its last snapshot, and carries on from the recorded inputs. Outputs are written exactly once across that failover, because a stale owner's append is refused and the new owner skips outputs that already landed.

Because the inputs are recorded, `felixd replay` can rebuild a workload as it was at any offset or time, and `felixd replay --diff --module` runs a different module over the same recorded inputs and reports the first output that differs. That is how you check an upgrade before it goes live.

So a decision made by ordinary code is already traceable and replayable. The trouble starts when a model is in the loop.

## Why a model breaks replay

Running a model again is not a replay. Inference is rarely bit-reproducible: GPU kernels add numbers in whatever order their threads finish, engines pick algorithms by hardware and batch size, SIMD width and thread count change results on CPUs too, and sampling draws random numbers. Two scores that are close can swap places. ONNX Runtime and llama.cpp both give slightly different results across machines and settings, which is fine for most uses and fatal for "would it do the same again?"

There are two ways around that. Record the answer and never compute it again, or make the computation deterministic so that computing it again is safe.

## Inference as an effect

The first way would treat a model call the way felixd plans to treat any call to the outside world. The workload doesn't call the model. It writes a request naming the model by content hash, the parameters and the input. An executor on a machine with the right hardware runs the model and writes the response back as a record. The workload reads that response as one more input.

On replay, the response is read from the log like any other input. No model is loaded and nobody is asked again. It doesn't matter that the GPU was replaced, the driver was upgraded or the model was deleted months ago. The answer it gave is on the record, along with the hash of the model that actually ran.

{{ <figure src="/img/blog/what-if-every-decision-could-be-replayed/1-replay-decision.svg" alt="A machine labelled model v3 is faded and dashed because it is gone. A faded dashed line runs from it down to one record in the log, which is amber and labelled answer. On the right, the cat sits happily on a machine labelled replay, with a curved arrow from the log up to it." caption="The model that made the call is gone, but its answer is a record. Replay reads it instead of asking again." /> }}

Then "why did the valve close?" has a mechanical answer. The close command names the step that produced it. That step's input was a model response, which names the model hash and the request. The request names the sensor reading it was about. Replaying from the snapshot before it reproduces the decision bit for bit.

## Small models inside the step

The second way would put a small model, a classifier or an anomaly detector, inside the WebAssembly step itself. Wasm floating point is deterministic apart from NaN bit patterns, which wasmtime can canonicalize, so a model with no threads, no relaxed SIMD and its weights covered by the module hash would land on the same bits on every replay. Integer-quantized models suit this best, since integer arithmetic is exact. It's slower than native code and there's no GPU, but it needs no round trip through the log, and it keeps working on a device that has lost its link. A device could run the small model locally and ask a larger one as an effect only when it's unsure.

## Trying a new model on old history

Once requests and responses are recorded, comparing models stops being a guess. Send the recorded requests to the new model, write its answers to a separate stream, run the workload over them, and diff the decisions against what actually happened. You'd get the first decision that changes and how many do. The new model's answers are recorded too, so the comparison can itself be replayed and cited later.

{{ <figure src="/img/blog/what-if-every-decision-could-be-replayed/2-shadow-model.svg" alt="One log of recorded inputs feeds two machines. The left one, v3 live, has the cat on it and its decisions are acted on. The right one, v4 shadow, is an empty outline and its decisions are recorded only. In the shadow's row of decisions, the fourth is amber and labelled first diff." caption="Shadowing a new model: same recorded inputs, its decisions recorded but never acted on, and the first one that differs pointed out." /> }}

After that it could shadow live traffic for a while, recording its answers and acting on none, and then take over at a recorded input offset. Every node would switch at the same point, even across a failover, and rolling back would be another switch at a later offset.

## Acting exactly once

Answering a question twice is harmless. Opening a valve twice may not be. felixd already writes each output once and refuses a stale owner, but the machine that drives the valve can still fail halfway. So the idea is that an executor records that it accepted a command before acting. If nobody accepted it by the deadline, the outcome is a timeout, nothing happened, and it's safe to try again. If it was accepted and the executor was lost, the outcome is unknown: it may have acted. That gets recorded as exactly that, never guessed into success or failure.

What to do with unknown is the workload's call. "Set the valve to 40%" is safe to repeat where "open it 10% more" isn't. A workload can wait for the next reading from the valve before deciding. A gateway can remember recent command ids and refuse repeats. And a device that can do none of those stops and asks a person.

## What I don't know yet

Most of this depends on pieces felixd doesn't have yet: effects with typed outcomes, a store for model files that are far too big to be single records, and a determinism contract strict enough to promise the same bits. The log round trip costs milliseconds, so a control loop that has to close faster than that stays on the device. And a fleet with poor links needs a way for a site's records to reach the rest of the cluster later.

If you run models on machines that act on the world and have opinions about any of this, the [proposal](https://github.com/GetFelix/ninelives/pull/37) is the place to argue.
