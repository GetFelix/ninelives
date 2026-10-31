+++
title = "What if every machine decision could be replayed?"
description = "An idea being explored, not something built: treat a model call as an effect, record its answer as an input, and replay the decision without asking the model again."
date = 2026-10-31
slug = "what-if-every-decision-could-be-replayed"

[extra]
card = "/img/blog/what-if-every-decision-could-be-replayed/1-replay-decision.svg"

[taxonomies]
tags = ["inference", "replay", "design"]
+++

A gateway on a pipeline closes a valve at 02:14. A camera on a line flags a good part as bad. A depot reroutes a van around a road that was open. In the morning someone asks the same four questions: what did the machine see, which model was running, what did the model say, and would it do the same again?

Usually the answer is a log line someone chose to write, a model that has since been redeployed, and a guess. This post is about how the answer could be a set of records instead. It's an idea for NineLives that I'm exploring, not something that exists. The [design](https://github.com/GetFelix/ninelives/blob/main/docs/inference-as-effect.md) has the details, and none of it is built.

## Running the model again isn't a replay

The obvious way to replay a decision is to feed the same input to the same model. That doesn't work, because inference is rarely reproducible bit for bit. A GPU adds numbers in whatever order its threads finish, and floating point addition isn't associative. Engines pick kernels by hardware, driver version and batch size, so a request batched with others can come out different from the same request alone. Even on a CPU, SIMD width and thread count change the order of additions, and ONNX Runtime and llama.cpp both give slightly different numbers across machines and settings. A language model sampling above zero temperature draws random numbers. And a fleet never upgrades its GPUs and drivers all at once.

Most of the time the differences are in the low bits. But when two scores are close they swap places, and then the replay says the valve stays open. A replay that can disagree with the original isn't evidence of anything.

## What felixd already does

NineLives runs workloads with [felixd](https://github.com/GetFelix/felixd), and part of the answer is already there. A felixd workload is a WebAssembly module, run under wasmtime, whose only inputs are records on Felix streams. It has no clock, no random numbers and no network, so the same records always produce the same state. When a machine is lost, another claims the workload, restores its last snapshot and continues from the recorded inputs, and outputs are written exactly once across that failover. `felixd replay` rebuilds a workload at any offset or time, and `felixd replay --diff --module` runs a different module over the same inputs and reports the first output that differs.

So decisions made by ordinary code are already replayable. The model is the part that isn't.

## The idea: the model call is an effect

What if the workload never called the model at all? It would write a request instead: the model, pinned by content hash in the workload's spec, the parameters, and a reference to the input. A separate executor service on a machine with the right hardware would pick it up, run the model, and write the response back as a record, with the hash of the model it actually loaded. The workload would read that response as one more input, in a recorded order.

On replay, nothing calls the model. The response is read from the log like any other input. It wouldn't matter that the GPU was swapped, the driver upgraded or the model deleted months ago. The answer the model gave is the answer on the record, so the replay lands on the same decision every time.

{{ <figure src="/img/blog/what-if-every-decision-could-be-replayed/1-replay-decision.svg" alt="A machine labelled model v3 is faded and dashed because it is gone. A faded dashed line runs from it down to one record in the log, which is amber and labelled answer. On the right, the cat sits happily on a machine labelled replay, with a curved arrow from the log up to it." caption="The model that made the call is gone, but its answer is a record. Replay reads it instead of asking again." /> }}

Keeping the executor out of felixd matters too. A driver fault or a model eating all the memory would crash the executor, not stall felixd's lease renewals, so a sick GPU wouldn't look like a lost machine.

The same log carries everything around the decision. The [companion post on getfelix.dev](https://getfelix.dev/blog/what-if-your-fleet-ran-on-one-log.html) looks at the Felix side: the devices' readings, the model's responses and the commands back out all on one log, under the same rules.

## Asking why

With the response recorded, "why did the valve close?" has a mechanical answer. The close command names the step that produced it. That step's input was a model response, which names the model hash, the parameters and the request. The request names the sensor reading it was about. Replaying from the snapshot before that reading reproduces the decision exactly.

That only works while the records are still there. Once a snapshot covers a response, resuming no longer needs it, and felixd's trim floors would let it go. That suits recovery and not an audit. So each workload would state an audit horizon in its spec, and Felix's head trim would never cut its responses below it. How long a decision stays explainable would be a choice someone wrote down, not a side effect of snapshot retention.

## Trying a new model on old history

A new model can't reuse old answers, since every request would name a different hash. But the recorded requests are still there. Send them to the new model, write its answers to a separate stream, run the workload over them, and diff the decisions against what actually happened. You'd see the first decision that changes and how many do. The new answers are recorded too, so the comparison can be replayed and cited later.

{{ <figure src="/img/blog/what-if-every-decision-could-be-replayed/2-shadow-model.svg" alt="One log of recorded inputs feeds two machines. The left one, v3 live, has the cat on it and its decisions are acted on. The right one, v4 shadow, is an empty outline and its decisions are recorded only. In the shadow's row of decisions, the fourth is amber and labelled first diff." caption="Shadowing a new model: same recorded inputs, its decisions recorded but never acted on, and the first one that differs pointed out." /> }}

After that the new model could shadow live traffic for a while, recording answers and acting on none, and then take over at a recorded input offset. Every node would switch at the same point, even across a failover.

## Acting once, or saying you don't know

Asking a model twice is harmless. Opening a valve twice may not be. A command to a device would be an effect too, and felixd already writes each output once and refuses a stale owner. What's left is the executor that fails halfway. So it would record that it accepted a command before acting. If nobody accepted the command by its deadline, the outcome is a timeout: nothing happened, and it's safe to try again. If it was accepted and the executor was lost, the outcome is unknown, meaning it may have acted. That gets recorded as exactly that, never guessed into success or failure, and replay sees the same thing.

What to do about unknown is up to the workload. "Set the valve to 40%" is safe to repeat where "open it 10% more" isn't. A workload can wait for the valve's next reading before deciding, a gateway can refuse a command id it has already seen, and a device that can do neither stops and asks a person.

## A smaller option: the model inside the step

Some models are small enough to skip the round trip: a classifier, an anomaly detector. Those could run inside the WebAssembly step itself. Wasm floating point is deterministic apart from NaN bit patterns, which wasmtime can canonicalize, so a model with no threads, no relaxed SIMD and its weights covered by the module hash would land on the same bits on every replay. Integer-quantized models suit it best. It's slower than native code and has no GPU, but it keeps working on a device that has lost its link. A device could run the small model locally and send an effect to a larger one only when it's unsure.

## What I don't know yet

Most of this depends on pieces felixd doesn't have: effects with typed outcomes, a store for model files far too big to be single records, and a determinism contract strict enough to promise the same bits for the in-step models. A round trip through the log costs milliseconds, so a control loop that has to close faster than that stays on the device. And a site with a poor link needs a way to get its records to the rest of the fleet later.

If you run models on machines that act on the world and have opinions about any of this, the [design](https://github.com/GetFelix/ninelives/blob/main/docs/inference-as-effect.md) and its open questions are the place to argue.
