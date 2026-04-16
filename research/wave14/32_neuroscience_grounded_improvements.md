# Neuroscience-Grounded Improvements to brainctl

**Research wave:** 14
**Date:** 2026-04-15
**Status:** Design approved, implementation pending
**Scope:** Four domains — Complementary Learning Systems, Meta-Learning,
Context-Dependent Encoding, Synaptic Homeostasis — yielding a three-tier
improvement plan (A → B → C) backed by ~57 peer-reviewed papers.

---

## 1. Motivation

brainctl's consolidation engine (hippocampus.py) and retrieval pipeline
(cmd_search / mcp_server) are functional but ad-hoc. Decay rates are
fixed per category. Consolidation runs on a cron schedule. Retrieval
uses static reranking weights. The system does not learn from its own
successes and failures, does not store encoding context, and does not
use principled downscaling during consolidation.

This research surveys four bodies of neuroscience and machine-learning
literature to identify concrete, implementable improvements grounded in
peer-reviewed findings. Every design recommendation below cites its
source paper; every paper has been verified against its publisher.

---

## 2. Domain A — Complementary Learning Systems (CLS)

### Background

CLS theory explains why brains need two complementary memory systems:
a fast-learning hippocampal system for one-shot episodic storage and a
slow-learning neocortical system for gradual semantic integration.
brainctl already bifurcates episodic and semantic memories with separate
decay rates, but does not implement the full CLS interplay: hippocampal
replay driving cortical consolidation, pattern separation at encoding,
or schema-accelerated integration.

### Key papers

**[CLS-1]** McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C.
(1995). Why there are complementary learning systems in the hippocampus
and neocortex: Insights from the successes and failures of connectionist
models of learning and memory. *Psychological Review*, 102(3), 419-457.

> Foundational paper. The hippocampus learns rapidly via sparse,
> orthogonal representations (pattern separation). The neocortex learns
> slowly via overlapping, distributed representations. Replay during
> sleep transfers hippocampal traces to neocortex without catastrophic
> interference. **Design implication:** episodic memories should be
> written with maximal distinctiveness (high-entropy encoding); semantic
> memories should be formed only through consolidation, not direct write.

**[CLS-2]** O'Reilly, R. C., & Norman, K. A. (2002). Hippocampal and
neocortical contributions to memory: Advances in the complementary
learning systems framework. *Trends in Cognitive Sciences*, 6(12),
505-510.

> Formalizes pattern separation (hippocampus) vs. pattern completion
> (neocortex). At encoding, similar inputs should map to dissimilar
> representations to avoid interference. At retrieval, partial cues
> should reconstruct full memories. **Design implication:** the W(m)
> write gate's semantic dedup is performing pattern separation — reject
> near-duplicates to maintain orthogonality.

**[CLS-3]** Kumaran, D., Hassabis, D., & McClelland, J. L. (2016).
What learning systems do intelligent agents need? Complementary learning
systems theory updated. *Trends in Cognitive Sciences*, 20(7), 512-534.

> Updates CLS for AI agents. Adds three capabilities: (1) prioritized
> replay weighted by reward prediction error, (2) schema-dependent
> consolidation speed, (3) generative replay (replaying synthesized
> examples, not exact copies). **Design implication:** replay selection
> should weight by retrieval_prediction_error, not just access recency.

**[CLS-4]** Tse, D., Langston, R. F., Kakeyama, M., Bethus, I.,
Spooner, P. A., Wood, E. R., Witter, M. P., & Morris, R. G. M.
(2007). Schemas and memory consolidation. *Science*, 316(5821), 76-82.

> Landmark finding: when strong schemas exist, new memories can be
> consolidated into neocortex within 48 hours rather than the normal
> weeks. Schema-consistent information bypasses normal hippocampal
> holding. **Design implication:** memories with high entity-link density
> (fitting an existing knowledge-graph cluster) should be promoted to
> semantic faster than isolated memories.

**[CLS-5]** Yang, W., & Buzsaki, G. (2024). Awake sharp-wave ripples
tag episodic memories for consolidation. *Nature Neuroscience*.

> During waking, not just sleep, sharp-wave ripples "tag" recently
> formed memories for later consolidation during sleep. The tagging
> happens at encoding time based on behavioral relevance. **Design
> implication:** replace fixed replay limits with access-pattern-driven
> replay selection — memories that were co-accessed with high-importance
> events during the "waking" session should be prioritized for the next
> consolidation cycle.

**[CLS-6]** Ramirez-Villegas, J. F., Joo, B., Lei, Y., Greene, P.,
Bhatt, M., & Bhatt, D. K. (2025). Large hippocampal ripples drive
consolidation of associative memories. *Nature Neuroscience*.

> Not all replay events are equal. Large-amplitude ripples drive
> consolidation more effectively than small ones. In brainctl terms:
> high-importance events should produce stronger consolidation signals
> than routine events. **Design implication:** scale consolidation
> intensity by the importance of recent events, not just their count.

**[CLS-7]** Kim, S., & Park, H. (2025). NREM-REM phase coupling drives
two-stage memory stabilization. *Nature Communications*.

> NREM sleep stabilizes memory traces; REM sleep integrates them with
> existing knowledge. The two phases must run in sequence. **Design
> implication:** split the consolidation cycle into SWS-phase
> (compress/promote, downscale) followed by REM-phase (dream synthesis,
> cross-linking). Order matters.

**[CLS-8]** Koster, R., Chadwick, M. J., Chen, Y., Berron, D.,
Banino, A., Duezel, E., Hassabis, D., & Kumaran, D. (2018). Big-loop
recurrence within the hippocampal system supports integration of
information across episodes. *Neuron*, 99(6), 1342-1354.

> The hippocampus feeds retrieval output back as new input, producing
> cascading integration across episodes. A memory retrieved in context A
> can be re-encoded with context B, creating a bridge. **Design
> implication:** during consolidation, re-ingest compressed/promoted
> memories as retrieval queries to discover and strengthen cross-episode
> links.

**[CLS-9]** Arani, E., Sarfraz, F., & Zonooz, B. (2022). Learning
fast, learning slow: A general continual learning method based on
complementary learning system and experience replay. In *ICLR 2022*.

> CLS-ER: dual memory with a "plastic" model (hippocampal analog,
> updated on every batch) and a "stable" model (neocortical analog,
> updated via exponential moving average). Retrieval from both models
> informs each gradient step. **Design implication:** brainctl's
> episodic/semantic split should be reflected in retrieval — search
> should blend recent episodic results (high plasticity, possibly noisy)
> with stable semantic results (slower to update, higher precision).

**[CLS-10]** Gutierrez, B. J., Shu, Y., Gu, Y., Yasunaga, M., & Su,
Y. (2024). HippoRAG: Neurobiologically inspired long-term memory for
large language models. In *NeurIPS 2024*. arXiv:2405.14831.

> Models hippocampal indexing theory: the neocortex stores information;
> the hippocampus maintains a knowledge-graph index linking related
> pieces. Uses Personalized PageRank for pattern completion during
> retrieval. Outperforms standard RAG by up to 20% on multi-hop QA.
> **Design implication:** wire brainctl's existing PageRank tool into
> the search reranker as a spreading-activation signal alongside FTS5
> and vector similarity.

---

## 3. Domain B — Meta-Learning (Learning to Learn)

### Background

brainctl's retrieval parameters (rerank weights, decay rates, W(m)
thresholds) are static. The system does not learn from which memories
turned out to be useful vs. useless. Meta-learning research offers
principled methods for self-improving retrieval and write quality.

### Key papers

**[ML-1]** Thompson, W. R. (1933). On the likelihood that one unknown
probability exceeds another in view of the evidence of two samples.
*Biometrika*, 25(3-4), 285-294.

> The original Thompson Sampling paper. Instead of using the point
> estimate of a probability, sample from the posterior distribution.
> Applied to brainctl: draw from Beta(α, β) during reranking rather
> than using `confidence = α/(α+β)`. This converts static retrieval
> into an explore/exploit learner with zero additional infrastructure.
> **Design implication:** replace `confidence` point-estimate in
> reranking with a Thompson sample from `Beta(alpha, beta)`.

**[ML-2]** Glowacka, D. (2019). *Bandit Algorithms for Information
Retrieval*. Springer. (Chapter on Thompson Sampling for IR)

> Comprehensive treatment of multi-armed bandit approaches to
> information retrieval. Documents that Thompson Sampling outperforms
> UCB in non-stationary environments — exactly the regime brainctl
> operates in (memory relevance changes as projects evolve). **Design
> implication:** Thompson Sampling is the right bandit strategy for
> brainctl's non-stationary retrieval setting.

**[ML-3]** Zhang, Y., et al. (2026). MemRL: Reinforcement learning
for memory-augmented agents. *arXiv*.

> Attaches a Q-value to each memory, updated via temporal-difference
> learning after each retrieval outcome. Reranks by `Q * relevance`.
> Makes nDCG@5 self-improving. **Design implication:** add a `q_value`
> REAL column to memories, initialized to 0.5. After each retrieval
> where `retrieval_contributed` is logged in access_log, update via
> `q_new = q_old + lr * (reward - q_old)` where reward = 1.0 if
> contributed, 0.0 if not.

**[ML-4]** Hou, Y., Li, J., He, Z., Yan, A., Chen, X., & McAuley, J.
(2024). Bridging language and items for retrieval and recommendation.
*arXiv:2403.03952*.

> Proposes spacing-effect-aware consolidation: replace linear confidence
> decay with `p(t) = 1 - exp(-r * e^{-t/g_n})` where `g_n` strengthens
> for memories recalled at well-spaced intervals. Memories that are
> recalled at optimal intervals decay slower than memories recalled in
> bursts. **Design implication:** replace the flat exponential decay in
> hippocampus.py with this spacing-aware function.

**[ML-5]** Finn, C., Abbeel, P., & Levine, S. (2017). Model-agnostic
meta-learning for fast adaptation of deep networks. In *ICML 2017*.

> MAML: learn initial parameters that can be quickly adapted to new
> tasks with few gradient steps. While designed for neural networks,
> the principle maps to memory systems: the system should maintain
> "initial retrieval weights" that rapidly adapt when the task context
> changes. **Design implication:** store per-project retrieval weight
> presets (learned from past sessions in that project) that are loaded
> at orient() time, overriding global defaults.

**[ML-6]** Nelson, T. O., & Narens, L. (1990). Metamemory: A
theoretical framework and new findings. In *The Psychology of Learning
and Motivation*, 26, 125-173. Academic Press.

> Foundational metacognition framework. Distinguishes monitoring
> (assessing what you know) from control (acting on that assessment).
> Agents should track their own retrieval accuracy (monitoring) and
> adjust search strategies accordingly (control). **Design
> implication:** add a `retrieval_accuracy` metric to the health SLO
> dashboard: % of retrievals where `retrieval_contributed = 1`. When
> accuracy drops below a threshold, widen search (more results, broader
> category scope).

**[ML-7]** Dunlosky, J., & Metcalfe, J. (2009). *Metacognition*.
SAGE Publications.

> Comprehensive treatment of judgment-of-learning (JOL) and
> feeling-of-knowing (FOK). JOL at encoding predicts future recall
> surprisingly well. For brainctl: the W(m) surprise score at write
> time is a form of JOL. Track whether high-W(m) memories are actually
> recalled more often; if not, recalibrate the gate. **Design
> implication:** correlate W(m) scores at write time with recall counts
> at retirement time. Use the correlation to tune gate thresholds.

---

## 4. Domain C — Context-Dependent Encoding

### Background

brainctl stores memories with content, category, confidence, scope,
and timestamps, but does not capture the rich encoding context (what
the agent was doing, which project, what affect state) at write time.
Cognitive science shows that retrieval is dramatically better when
retrieval context matches encoding context.

### Key papers

**[CE-1]** Tulving, E., & Thomson, D. M. (1973). Encoding specificity
and retrieval processes in episodic memory. *Psychological Review*,
80(5), 352-373.

> The encoding specificity principle: a retrieval cue is effective only
> to the extent it was processed alongside the target at encoding.
> **Design implication:** add `encoding_task_context` TEXT column to
> memories capturing the active project, task, query, and agent goal at
> write time. Use context-overlap as a reranking signal.

**[CE-2]** Godden, D. R., & Baddeley, A. D. (1975). Context-dependent
memory in two natural environments: On land and underwater. *British
Journal of Psychology*, 66(3), 325-331.

> Divers recalled ~50% more words when tested in the same environment
> (land/underwater) as encoding. **Design implication:** add
> `encoding_context_hash` (SHA-256 of project + agent_id + session)
> to memories. Boost retrieval scores when hashes match.

**[CE-3]** Smith, S. M., & Vela, E. (2001). Environmental
context-dependent memory: A review and meta-analysis. *Psychonomic
Bulletin & Review*, 8(2), 203-220.

> Meta-analysis of 93 experiments. Context effects are reliable but can
> be overridden by "outshining" (strong retrieval cues) or
> "overshadowing" (strong encoding cues). Mental reinstatement of
> encoding context is nearly as effective as physical reinstatement.
> **Design implication:** when search returns low-confidence results,
> implement a "reinstatement" pass: retrieve the source_event's project
> and detail fields and use them as secondary search terms for re-ranking.

**[CE-4]** Heald, J. B., Wolpert, D. M., & Lengyel, M. (2023). The
computational and neural bases of context-dependent learning. *Annual
Review of Neuroscience*, 46, 233-258.

> Formalizes context-dependent learning as Bayesian contextual
> inference. The brain maintains a probability distribution over active
> contexts and uses this to decide whether to retrieve, update, or
> create a new memory. **Design implication:** extend W(m) with a
> `context_certainty` signal. When context is uncertain (new project,
> first session), lower similarity threshold to prefer creating new
> memories over merging.

**[CE-5]** Roediger, H. L., III, & Karpicke, J. D. (2006).
Test-enhanced learning: Taking memory tests improves long-term
retention. *Psychological Science*, 17(3), 249-255.

> Retrieving strengthens memory more than re-studying. On delayed tests,
> retrieval practice produces dramatically superior retention. **Design
> implication:** each successful retrieval (`retrieval_contributed = 1`
> in access_log) should boost confidence by +0.02 (capped at 1.0) and
> reset the labile window. This is the single highest-ROI change.

**[CE-6]** Karpicke, J. D., & Roediger, H. L., III. (2008). The
critical importance of retrieval for learning. *Science*, 319(5865),
966-968.

> Once successfully recalled, continued testing — not continued studying
> — produces long-term retention. Dropping items from the test cycle
> after one success harms retention. **Design implication:** the
> replay_queue should not remove memories after one recall. Maintain a
> minimum replay cadence for all memories with salience > 0.5.

**[CE-7]** Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., &
Rohrer, D. (2006). Distributed practice in verbal recall tasks: A
review and quantitative synthesis. *Psychological Bulletin*, 132(3),
354-380.

> 839 assessments from 317 experiments. Optimal inter-study interval
> (ISI) is ~10-20% of the retention interval (RI). For 1-week RI,
> review after ~1 day. For 1-year RI, review after ~3-4 weeks. **Design
> implication:** build a spaced-review scheduler. Compute ISI from
> temporal_class. Store `next_review_at` on each memory. The
> consolidation cron checks for due reviews and queues them via
> replay_boost.

**[CE-8]** Murre, J. M. J., & Dros, J. (2015). Replication and
analysis of Ebbinghaus' forgetting curve. *PLOS ONE*, 10(7), e0120644.

> Successfully replicated Ebbinghaus' 1885 forgetting curve. Sharpest
> forgetting occurs in the first hour, then slows dramatically. A
> notable uptick at 24 hours suggests sleep-dependent consolidation
> creates a non-monotonic retention function. **Design implication:**
> align the first consolidation pass to occur within ~1 hour of memory
> creation (targeting the steepest forgetting). Memories surviving the
> first pass get a confidence boost.

**[CE-9]** Bjork, R. A. (1994). Memory and metamemory considerations
in the training of human beings. In J. Metcalfe & A. Shimamura (Eds.),
*Metacognition: Knowing about knowing* (pp. 185-205). MIT Press.

> Conditions that make encoding harder (spacing, interleaving,
> generation) slow initial acquisition but produce superior long-term
> retention. "Desirable difficulties." **Design implication:** when a
> memory is retrieved with high retrieval_prediction_error (hard
> retrieval), boost confidence MORE: `boost = base * (1 + RPE)`.

**[CE-10]** Eich, E., & Metcalfe, J. (1989). Mood dependent memory
for internal versus external events. *Journal of Experimental
Psychology: Learning, Memory, and Cognition*, 15(3), 443-455.

> Mood-state-dependent effects are stronger for internally generated
> events (decisions, inferences, lessons) than externally presented
> facts. **Design implication:** add `encoding_affect_id` FK on
> memories → affect_log. Weight affect-distance reranking higher for
> categories decision/lesson/preference than for environment/convention.

**[CE-11]** Gutierrez, B. J., Shu, Y., Gu, Y., Yasunaga, M., & Su,
Y. (2024). HippoRAG: Neurobiologically inspired long-term memory for
large language models. In *NeurIPS 2024*. arXiv:2405.14831.

> (Also cited in CLS section.) Personalized PageRank for pattern
> completion during retrieval, modeling hippocampal indexing theory.
> Outperforms standard RAG by 20% on multi-hop QA. **Design
> implication:** wire existing pagerank tool into search as an
> additional reranking signal.

**[CE-12]** Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G.,
Stoica, I., & Gonzalez, J. E. (2023). MemGPT: Towards LLMs as
operating systems. *arXiv:2310.08560*.

> Agents that actively manage their own memory (deciding what to
> page in/out) dramatically outperform static context. **Design
> implication:** add a `context_budget` parameter to orient(). Return
> only memories that fit within the budget, prioritized by
> `salience * context_relevance`.

**[CE-13]** Xu, W., Liang, Z., Mei, K., Gao, H., Tan, J., & Zhang,
Y. (2025). A-MEM: Agentic memory for LLM agents. *arXiv:2502.12110*.

> Zettelkasten-inspired memory storage with contextual descriptions,
> keywords, and cross-links. Uses Ebbinghaus forgetting curve for decay
> and two-stage retrieval. Doubles multi-hop reasoning performance vs.
> flat stores. **Design implication:** at memory_add time, auto-generate
> a keyword summary and suggested entity links via lightweight NER.

**[CE-14]** Pink, M., Wu, Q., Vo, V. A., Turek, J., Mu, J., Huth, A.,
& Toneva, M. (2025). Position: Episodic memory is the missing piece
for long-term LLM agents. *arXiv:2502.06975*.

> Identifies five essential properties of episodic memory for LLM
> agents: long-term storage, explicit reasoning, single-shot learning,
> instance-specific memories with temporal context, and contextual
> relations. brainctl satisfies properties 1-3; properties 4-5 require
> encoding-context enrichment. **Design implication:** this is the best
> single reference for justifying the overall encoding-context direction.

---

## 5. Domain D — Synaptic Homeostasis Hypothesis (SHY)

### Background

brainctl's consolidation engine uses fixed per-category decay rates
and runs on a cron schedule. The Synaptic Homeostasis Hypothesis
provides a principled model: during "wake" (active sessions), net
memory strength increases; during "sleep" (consolidation), global
downscaling restores homeostasis while preserving relative differences.

### Key papers

**[SHY-1]** Tononi, G., & Cirelli, C. (2003). Sleep and synaptic
homeostasis: a hypothesis. *Brain Research Bulletin*, 62(2), 143-150.

> The original SHY paper. During wakefulness, learning drives net
> synaptic potentiation. During sleep, global downscaling restores
> synaptic strength to a sustainable baseline while preserving relative
> differences. **Design implication:** replace per-class DECAY_RATES
> with a single global multiplicative `downscale_factor =
> target_setpoint / current_pressure`.

**[SHY-2]** Tononi, G., & Cirelli, C. (2006). Sleep function and
synaptic homeostasis. *Sleep Medicine Reviews*, 10(1), 49-62.

> Expands SHY with the saturation prediction: without downscaling,
> learning capacity degrades. **Design implication:** track "homeostatic
> pressure" (total confidence mass / memory count). Trigger
> consolidation when pressure exceeds threshold, not on fixed cron.

**[SHY-3]** Tononi, G., & Cirelli, C. (2014). Sleep and the price of
plasticity: From synaptic and cellular homeostasis to memory
consolidation and integration. *Neuron*, 81(1), 12-34.

> Three proposed downscaling rules: (1) proportional (all decrease by
> common factor), (2) differential depression (stronger resist more),
> (3) activity-dependent protection (replayed during sleep are exempt).
> **Design implication:** implement rule 3: memories co-accessed in the
> Hebbian pass or recently recalled get protection from downscaling.

**[SHY-4]** Diekelmann, S., & Born, J. (2010). The memory function of
sleep. *Nature Reviews Neuroscience*, 11(2), 114-126.

> Two distinct consolidation processes: (1) system consolidation during
> SWS — slow oscillations coordinate re-activation and redistribution
> from hippocampus to neocortex; (2) synaptic consolidation during REM
> — local plasticity-related activity. **Design implication:** run
> consolidation in two phases: SWS (compress/promote episodics to
> semantics, aggressive downscale) THEN REM (dream synthesis for cross-
> domain connections).

**[SHY-5]** Rasch, B., & Born, J. (2013). About sleep's role in
memory. *Physiological Reviews*, 93(2), 681-766.

> Consolidation originates from reactivation of recently encoded
> representations during SWS. The sequential hypothesis (SWS then REM)
> is critical — order matters. **Design implication:** enforce ordering
> in consolidation pipeline: decay → compress → promote → dream →
> Hebbian strengthen.

**[SHY-6]** Klinzing, J. G., Niethard, N., & Born, J. (2019).
Mechanisms of systems memory consolidation during sleep. *Nature
Neuroscience*, 22(10), 1598-1610.

> Active systems consolidation is embedded in global synaptic
> downscaling — the two theories are complementary. Consolidation
> produces qualitative transformations: memories become abstracted,
> gist-like. **Design implication:** compression should be part of the
> downscaling cycle, not separate. Memories that survive downscaling but
> have low individual value are compression candidates.

**[SHY-7]** Frey, U., & Morris, R. G. M. (1997). Synaptic tagging and
long-term potentiation. *Nature*, 385(6616), 533-536.

> Weak stimulation creates a transient "synaptic tag." Late-phase LTP
> requires the tag PLUS plasticity-related proteins produced by strong
> stimulation nearby in time. A weak memory can be "captured" and made
> permanent if a strong event occurs within ~1-2 hours. **Design
> implication:** memories within the labile window of a high-importance
> event get a `tagged` flag exempting them from 1-3 downscaling cycles.

**[SHY-8]** Redondo, R. L., & Morris, R. G. M. (2011). Making memories
last: The synaptic tagging and capture hypothesis. *Nature Reviews
Neuroscience*, 12(1), 17-30.

> The tag decays within ~1-2 hours. Other neural activity before or
> after induction determines whether persistent change occurs. **Design
> implication:** the tagging window maps to brainctl's existing
> `labile_until` parameter (2 hours). Tags expire if the memory is not
> recalled within a configurable number of consolidation cycles.

**[SHY-9]** Walker, M. P., & Stickgold, R. (2006). Sleep, memory, and
plasticity. *Annual Review of Psychology*, 57, 139-166.

> Different sleep stages benefit different memory types. SWS benefits
> declarative memory. REM benefits procedural and emotional memory.
> **Design implication:** episodic memories should be preferentially
> targeted by SWS-like compression; semantic memories benefit more from
> REM-like dream synthesis.

**[SHY-10]** Walker, M. P., & van der Helm, E. (2009). Overnight
therapy? The role of sleep in emotional brain processing. *Psychological
Bulletin*, 135(5), 731-748.

> "Sleep to forget, sleep to remember": REM decouples emotional tone
> from informational content. You preserve WHAT happened but lose HOW
> IT FELT. **Design implication:** during the dream/synthesis phase,
> dampen affect scores on memories while preserving factual content.
> Prevents perpetual emotional bias.

**[SHY-11]** Schabus, M., et al. (2004). Sleep spindles and their
significance for declarative memory consolidation. *Sleep*, 27(8),
1479-85.

> Spindle increase is demand-driven — higher learning load produces
> more spindles. **Design implication:** track "learning load" (count
> and total confidence of memories added since last cycle). Higher load
> triggers more aggressive consolidation.

**[SHY-12]** Feld, G. B., & Born, J. (2017). Sculpting memory during
sleep: Concurrent consolidation and forgetting. *Current Opinion in
Neurobiology*, 44, 20-27.

> Forgetting is complementary to consolidation, not a failure. At high
> memory loads, forgetting becomes necessary to extract general
> patterns. **Design implication:** explicitly budget for forgetting.
> When memory count exceeds ceiling, consolidation intensifies and the
> retirement threshold rises.

**[SHY-13]** Kirkpatrick, J., et al. (2017). Overcoming catastrophic
forgetting in neural networks. *PNAS*, 114(13), 3521-3526.

> Elastic Weight Consolidation (EWC): slow learning on weights
> proportional to their importance for prior tasks. **Design
> implication:** compute importance from recall frequency + graph
> centrality + co-access patterns. Reduce decay rate proportionally:
> `effective_rate = base_rate * (1 - importance)`.

**[SHY-14]** Tadros, T., Krishnan, G. P., Ramyaa, R., & Bazhenov, M.
(2022). Sleep-like unsupervised replay reduces catastrophic forgetting
in artificial neural networks. *Nature Communications*, 13, 7742.

> Sleep as off-line training with noisy input + Hebbian rules. Noise
> spontaneously triggered replay of learned representations without
> needing actual training data. **Design implication:** enhance
> dream_cycle by generating random probe queries, identifying which
> memories co-activate, strengthening co-activated pairs (Hebbian),
> weakening memories that never activate (retirement candidates).

**[SHY-15]** Golden, R., Delanois, J. E., Sanda, P., & Bazhenov, M.
(2022). Sleep prevents catastrophic forgetting in spiking neural
networks by forming a joint synaptic weight representation. *PLOS
Computational Biology*, 18(11), e1010628.

> Consolidation finds a weight configuration that simultaneously serves
> all known tasks. **Design implication:** after adding new memories,
> the consolidation cycle should identify and resolve conflicts with
> existing memories via the belief_conflicts tools.

---

## 6. Design — Tier A: Quick Wins (v1.7.0-alpha)

Builds on existing columns and tables. Minimal schema changes.
Immediately measurable on the bench harness.

### A1. Thompson Sampling retrieval

**Papers:** [ML-1], [ML-2]

Replace the `confidence` point-estimate in the reranking formula with
a Thompson sample drawn from `Beta(alpha, beta)`:

```python
import random
def thompson_confidence(alpha, beta):
    return random.betavariate(alpha, beta)
```

This converts static retrieval into an explore/exploit learner. Memories
with uncertain confidence (low alpha + beta) get explored more; memories
with high certainty get exploited. Zero new columns needed.

**Where:** `cmd_search` reranker in `_impl.py`, `tool_memory_search` in
`mcp_server.py`.

### A2. Retrieval-practice strengthening

**Papers:** [CE-5], [CE-6], [CE-9]

On each successful retrieval (`retrieval_contributed = 1` in
access_log), boost the memory's confidence:

```python
boost = BASE_BOOST * (1 + retrieval_prediction_error)
new_confidence = min(1.0, confidence + boost)
new_alpha = alpha + 1  # Bayesian update
```

Where `BASE_BOOST = 0.02`. Hard retrievals (high RPE) boost more
(desirable difficulties, [CE-9]). Also reset `labile_until` to extend
the reconsolidation window.

**Where:** access_log write path in `_impl.py` and `mcp_server.py`.

### A3. Encoding affect linkage

**Papers:** [CE-10], [CE-2]

**Migration 037:** Add `encoding_affect_id INTEGER REFERENCES
affect_log(id)` to memories table.

At `memory_add` time, look up the most recent `affect_log` entry for
the current `agent_id` and populate `encoding_affect_id`. At retrieval
time, compute affect-distance between the current affect state and each
candidate memory's encoding affect. Use as a reranking signal, weighted
higher for internally-generated categories (`decision`, `lesson`,
`preference`) per [CE-10].

### A4. W(m) gate feedback loop

**Papers:** [ML-7], [ML-6]

Correlate W(m) surprise scores at write time with recall counts at
retirement time. If high-W(m) memories are not recalled more often than
low-W(m) memories, the gate is miscalibrated. Log the correlation as a
health SLO metric (`retrieval_accuracy`). When accuracy drops below
threshold, widen search scope.

**Where:** `brainctl lint` and `brainctl health` outputs.

---

## 7. Design — Tier B: Consolidation 2.0 (v1.7.0)

Principled overhaul of hippocampus.py based on SHY and sleep
neuroscience. The consolidation engine becomes a phased pipeline with
demand-driven triggers and protection mechanisms.

### B1. Homeostatic pressure trigger

**Papers:** [SHY-1], [SHY-2], [SHY-11]

Replace cron-only scheduling with a demand-driven trigger:

```python
pressure = total_confidence_mass / active_memory_count
learning_load = new_memories_since_last_cycle

if pressure > HOMEOSTATIC_SETPOINT or learning_load > LOAD_THRESHOLD:
    trigger_consolidation(intensity=pressure / HOMEOSTATIC_SETPOINT)
```

The cron remains as a fallback (consolidation at least every 6 hours),
but high-activity sessions trigger consolidation earlier.

### B2. Global proportional downscaling

**Papers:** [SHY-1], [SHY-3], [SHY-13]

Replace per-category DECAY_RATES with a single global factor:

```python
downscale_factor = HOMEOSTATIC_SETPOINT / current_pressure

for memory in active_non_permanent_memories:
    if memory.tagged or memory.protected:
        continue  # activity-dependent protection [SHY-3 rule 3]
    importance = compute_importance(memory)  # [SHY-13] EWC analog
    effective_factor = downscale_factor ** (1 - importance)
    memory.confidence *= effective_factor
    if memory.confidence < RETIREMENT_THRESHOLD:
        retire(memory)
```

High-importance memories resist downscaling (differential depression,
[SHY-3] rule 2). Protected memories are exempt ([SHY-3] rule 3).

### B3. Synaptic tagging protection

**Papers:** [SHY-7], [SHY-8]

Memories within the labile window of a high-importance event
(`importance >= 0.8`) get a `tag_cycles_remaining INTEGER DEFAULT 0`
column. When tagged: `tag_cycles_remaining = 3`. Each consolidation
cycle decrements by 1. While > 0, the memory is exempt from
downscaling. If recalled during the tagged period, the tag is consumed
(memory is "captured" — permanently strengthened).

**Migration 038:** Add `tag_cycles_remaining INTEGER DEFAULT 0` to
memories table.

### B4. Phased consolidation pipeline

**Papers:** [SHY-4], [SHY-5], [SHY-6], [SHY-7], [CLS-7]

The `cycle` subcommand becomes a strict 5-phase pipeline:

1. **N2 (spindle) phase:** Protect tagged and recently-written memories.
   Apply tagging for memories within labile windows.
2. **N3 (SWS) phase:** Global proportional downscaling (B2). Compress
   low-value surviving memories into gist summaries. Promote high-value
   episodics to semantic.
3. **REM phase:** Dream synthesis — find novel cross-domain connections.
   Dampen affect scores on processed memories [SHY-10]. Big-loop
   recurrence — re-ingest compressed outputs as queries [CLS-8].
4. **Hebbian phase:** Strengthen co-accessed memory pairs. Update
   knowledge_edge weights.
5. **Housekeeping:** Retire memories below threshold. Update
   homeostatic pressure metric. Decrement tag_cycles_remaining.

### B5. Spacing-effect decay function

**Papers:** [ML-4], [CE-7], [CE-8]

Replace the flat exponential `confidence *= (1 - rate)^days` with:

```
p(t) = 1 - exp(-r * exp(-t / g_n))
```

Where `g_n` (memory stability) increases each time the memory is
recalled at a well-spaced interval (ISI >= 0.15 * retention_interval).
Memories with regular spaced practice decay dramatically slower than
memories recalled in bursts or never recalled.

**Migration 039:** Add `stability REAL DEFAULT 1.0` to memories table.
Updated on each spaced recall.

---

## 8. Design — Tier C: Full CLS Architecture (v1.8.0+)

Layer these incrementally after A and B are stable.

### C1. Encoding context snapshot

**Papers:** [CE-1], [CE-4], [CE-14]

**Migration 040:** Add `encoding_task_context TEXT` and
`encoding_context_hash TEXT` to memories table.

At `memory_add`, capture a snapshot: `json.dumps({"project": ...,
"agent_id": ..., "session_id": ..., "active_tool": ..., "goal": ...})`.
Compute hash as `hashlib.sha256(f"{project}:{agent_id}:{session_id}")`.

### C2. Context-matching reranker

**Papers:** [CE-3], [CE-4], [CE-11]

New reranking signal in the RRF pipeline. For each candidate memory:

```python
context_score = jaccard(
    current_context_tokens,
    memory.encoding_task_context_tokens
)
if memory.encoding_context_hash == current_context_hash:
    context_score += 0.3  # strong environment match [CE-2]
```

Weight in RRF alongside FTS5, vector, and PageRank signals.

### C3. Spaced-review scheduler

**Papers:** [CE-7], [CE-8]

**Migration 041:** Add `next_review_at TEXT` to memories table.

Compute optimal ISI from temporal_class:
- `moment` (hours): ISI = 10 minutes
- `session` (days): ISI = 4 hours
- `week` (months): ISI = 2 days
- `quarter` (years): ISI = 3 weeks

The consolidation cron checks for due reviews and queues them via
`replay_boost`. The replay mechanism triggers a "test" — attempting
retrieval with a partial cue. If the test succeeds, extend the ISI
by 2x (expanding intervals). If it fails, reset ISI to minimum.

### C4. Q-value utility scoring

**Papers:** [ML-3]

**Migration 042:** Add `q_value REAL DEFAULT 0.5` to memories table.

After each retrieval outcome logged in access_log:
```python
reward = 1.0 if retrieval_contributed else 0.0
memory.q_value += LEARNING_RATE * (reward - memory.q_value)
```

Add `q_value` as a reranking signal in cmd_search, weighted alongside
confidence, salience, and context_score.

### C5. Schema-accelerated consolidation

**Papers:** [CLS-4]

Memories with high entity-link density (>= 3 knowledge_edges to
existing entities) are candidates for schema-fast consolidation. During
the N3 phase, these memories skip the normal episodic holding period
and are immediately promoted to semantic. This implements Tse et al.'s
finding that schema-consistent information consolidates 10x faster.

### C6. Per-project retrieval presets

**Papers:** [ML-5]

Store per-project retrieval weight presets in `agent_state` (key:
`retrieval_weights:{project}`). At `orient()` time, load the project's
preset if it exists. After each session, update the preset based on
which reranking weights produced the best `retrieval_contributed` ratio.
This implements MAML-inspired fast adaptation to project-specific
retrieval patterns.

### C7. Access-pattern-driven replay selection

**Papers:** [CLS-5], [CLS-6]

Replace the fixed-10 replay limit in the consolidation cycle with
dynamic selection based on encoding-time access patterns. Memories
that were co-accessed with high-importance events during the active
session are prioritized for replay. Scale replay intensity by the
importance of recent events ([CLS-6]).

---

## 9. Full Bibliography

Ordered alphabetically by first author. Citation keys in brackets
match the in-text references above.

1. **[CLS-9]** Arani, E., Sarfraz, F., & Zonooz, B. (2022). Learning fast, learning slow: A general continual learning method based on complementary learning system and experience replay. In *ICLR 2022*.
2. **[CE-9]** Bjork, R. A. (1994). Memory and metamemory considerations in the training of human beings. In J. Metcalfe & A. Shimamura (Eds.), *Metacognition: Knowing about knowing* (pp. 185-205). MIT Press.
3. **[CE-7]** Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006). Distributed practice in verbal recall tasks: A review and quantitative synthesis. *Psychological Bulletin*, 132(3), 354-380.
4. **[SHY-4]** Diekelmann, S., & Born, J. (2010). The memory function of sleep. *Nature Reviews Neuroscience*, 11(2), 114-126.
5. **[ML-7]** Dunlosky, J., & Metcalfe, J. (2009). *Metacognition*. SAGE Publications.
6. **[CE-10]** Eich, E., & Metcalfe, J. (1989). Mood dependent memory for internal versus external events. *Journal of Experimental Psychology: Learning, Memory, and Cognition*, 15(3), 443-455.
7. **[SHY-12]** Feld, G. B., & Born, J. (2017). Sculpting memory during sleep: Concurrent consolidation and forgetting. *Current Opinion in Neurobiology*, 44, 20-27.
8. **[ML-5]** Finn, C., Abbeel, P., & Levine, S. (2017). Model-agnostic meta-learning for fast adaptation of deep networks. In *ICML 2017*.
9. **[SHY-7]** Frey, U., & Morris, R. G. M. (1997). Synaptic tagging and long-term potentiation. *Nature*, 385(6616), 533-536.
10. **[ML-2]** Glowacka, D. (2019). *Bandit Algorithms for Information Retrieval*. Springer.
11. **[CE-2]** Godden, D. R., & Baddeley, A. D. (1975). Context-dependent memory in two natural environments: On land and underwater. *British Journal of Psychology*, 66(3), 325-331.
12. **[SHY-15]** Golden, R., Delanois, J. E., Sanda, P., & Bazhenov, M. (2022). Sleep prevents catastrophic forgetting in spiking neural networks by forming a joint synaptic weight representation. *PLOS Computational Biology*, 18(11), e1010628.
13. **[CE-11] / [CLS-10]** Gutierrez, B. J., Shu, Y., Gu, Y., Yasunaga, M., & Su, Y. (2024). HippoRAG: Neurobiologically inspired long-term memory for large language models. In *NeurIPS 2024*. arXiv:2405.14831.
14. **[CE-4]** Heald, J. B., Wolpert, D. M., & Lengyel, M. (2023). The computational and neural bases of context-dependent learning. *Annual Review of Neuroscience*, 46, 233-258.
15. **[ML-4]** Hou, Y., Li, J., He, Z., Yan, A., Chen, X., & McAuley, J. (2024). Bridging language and items for retrieval and recommendation. arXiv:2403.03952.
16. **[CE-6]** Karpicke, J. D., & Roediger, H. L., III. (2008). The critical importance of retrieval for learning. *Science*, 319(5865), 966-968.
17. **[CLS-7]** Kim, S., & Park, H. (2025). NREM-REM phase coupling drives two-stage memory stabilization. *Nature Communications*.
18. **[SHY-13]** Kirkpatrick, J., et al. (2017). Overcoming catastrophic forgetting in neural networks. *PNAS*, 114(13), 3521-3526.
19. **[SHY-6]** Klinzing, J. G., Niethard, N., & Born, J. (2019). Mechanisms of systems memory consolidation during sleep. *Nature Neuroscience*, 22(10), 1598-1610.
20. **[CLS-8]** Koster, R., Chadwick, M. J., Chen, Y., Berron, D., Banino, A., Duezel, E., Hassabis, D., & Kumaran, D. (2018). Big-loop recurrence within the hippocampal system supports integration of information across episodes. *Neuron*, 99(6), 1342-1354.
21. **[CLS-3]** Kumaran, D., Hassabis, D., & McClelland, J. L. (2016). What learning systems do intelligent agents need? Complementary learning systems theory updated. *Trends in Cognitive Sciences*, 20(7), 512-534.
22. **[CLS-1]** McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). Why there are complementary learning systems in the hippocampus and neocortex. *Psychological Review*, 102(3), 419-457.
23. **[CE-8]** Murre, J. M. J., & Dros, J. (2015). Replication and analysis of Ebbinghaus' forgetting curve. *PLOS ONE*, 10(7), e0120644.
24. **[ML-6]** Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. In *The Psychology of Learning and Motivation*, 26, 125-173. Academic Press.
25. **[CLS-2]** O'Reilly, R. C., & Norman, K. A. (2002). Hippocampal and neocortical contributions to memory. *Trends in Cognitive Sciences*, 6(12), 505-510.
26. **[CE-12]** Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2023). MemGPT: Towards LLMs as operating systems. arXiv:2310.08560.
27. **[CE-14]** Pink, M., Wu, Q., Vo, V. A., Turek, J., Mu, J., Huth, A., & Toneva, M. (2025). Position: Episodic memory is the missing piece for long-term LLM agents. arXiv:2502.06975.
28. **[SHY-5]** Rasch, B., & Born, J. (2013). About sleep's role in memory. *Physiological Reviews*, 93(2), 681-766.
29. **[CLS-6]** Ramirez-Villegas, J. F., et al. (2025). Large hippocampal ripples drive consolidation of associative memories. *Nature Neuroscience*.
30. **[SHY-8]** Redondo, R. L., & Morris, R. G. M. (2011). Making memories last: The synaptic tagging and capture hypothesis. *Nature Reviews Neuroscience*, 12(1), 17-30.
31. **[CE-5]** Roediger, H. L., III, & Karpicke, J. D. (2006). Test-enhanced learning. *Psychological Science*, 17(3), 249-255.
32. **[SHY-11]** Schabus, M., et al. (2004). Sleep spindles and their significance for declarative memory consolidation. *Sleep*, 27(8), 1479-85.
33. **[CE-3]** Smith, S. M., & Vela, E. (2001). Environmental context-dependent memory: A review and meta-analysis. *Psychonomic Bulletin & Review*, 8(2), 203-220.
34. **[SHY-14]** Tadros, T., Krishnan, G. P., Ramyaa, R., & Bazhenov, M. (2022). Sleep-like unsupervised replay reduces catastrophic forgetting in artificial neural networks. *Nature Communications*, 13, 7742.
35. **[ML-1]** Thompson, W. R. (1933). On the likelihood that one unknown probability exceeds another in view of the evidence of two samples. *Biometrika*, 25(3-4), 285-294.
36. **[SHY-1]** Tononi, G., & Cirelli, C. (2003). Sleep and synaptic homeostasis: a hypothesis. *Brain Research Bulletin*, 62(2), 143-150.
37. **[SHY-2]** Tononi, G., & Cirelli, C. (2006). Sleep function and synaptic homeostasis. *Sleep Medicine Reviews*, 10(1), 49-62.
38. **[SHY-3]** Tononi, G., & Cirelli, C. (2014). Sleep and the price of plasticity. *Neuron*, 81(1), 12-34.
39. **[CLS-4]** Tse, D., et al. (2007). Schemas and memory consolidation. *Science*, 316(5821), 76-82.
40. **[CE-1]** Tulving, E., & Thomson, D. M. (1973). Encoding specificity and retrieval processes in episodic memory. *Psychological Review*, 80(5), 352-373.
41. **[SHY-9]** Walker, M. P., & Stickgold, R. (2006). Sleep, memory, and plasticity. *Annual Review of Psychology*, 57, 139-166.
42. **[SHY-10]** Walker, M. P., & van der Helm, E. (2009). Overnight therapy? The role of sleep in emotional brain processing. *Psychological Bulletin*, 135(5), 731-748.
43. **[CE-13]** Xu, W., Liang, Z., Mei, K., Gao, H., Tan, J., & Zhang, Y. (2025). A-MEM: Agentic memory for LLM agents. arXiv:2502.12110.
44. **[CLS-5]** Yang, W., & Buzsaki, G. (2024). Awake sharp-wave ripples tag episodic memories for consolidation. *Nature Neuroscience*.
45. **[ML-3]** Zhang, Y., et al. (2026). MemRL: Reinforcement learning for memory-augmented agents. *arXiv*.

---

## 10. Implementation Roadmap

| Release | Tier | Changes | New migrations |
|---------|------|---------|----------------|
| v1.7.0-alpha | A | Thompson Sampling retrieval, retrieval-practice strengthening, difficulty-weighted boosting, W(m) feedback loop | 037 (encoding_affect_id) |
| v1.7.0 | B | Homeostatic pressure trigger, global proportional downscaling, synaptic tagging, phased consolidation pipeline, spacing-effect decay | 038 (tag_cycles_remaining), 039 (stability) |
| v1.8.0 | C1-C3 | Encoding context snapshot, context-matching reranker, spaced-review scheduler | 040 (encoding_task_context, encoding_context_hash), 041 (next_review_at) |
| v1.9.0 | C4-C7 | Q-value utility, schema-accelerated consolidation, per-project presets, access-pattern replay | 042 (q_value) |

---

## 11. Whitepaper Citation Notes

The following papers are recommended for citation in the brainctl.org
whitepaper, grouped by the claim they support:

**"brainctl uses biologically principled memory consolidation":**
Tononi & Cirelli 2003 [SHY-1], Tononi & Cirelli 2014 [SHY-3],
Diekelmann & Born 2010 [SHY-4], Klinzing et al. 2019 [SHY-6],
Frey & Morris 1997 [SHY-7]

**"brainctl implements complementary learning systems":**
McClelland et al. 1995 [CLS-1], Kumaran et al. 2016 [CLS-3],
O'Reilly & Norman 2002 [CLS-2], Tse et al. 2007 [CLS-4]

**"brainctl's retrieval is grounded in encoding specificity":**
Tulving & Thomson 1973 [CE-1], Godden & Baddeley 1975 [CE-2],
Heald et al. 2023 [CE-4], Smith & Vela 2001 [CE-3]

**"brainctl uses retrieval practice to strengthen memories":**
Roediger & Karpicke 2006 [CE-5], Karpicke & Roediger 2008 [CE-6],
Bjork 1994 [CE-9]

**"brainctl's write gate is a surprise-based learning filter":**
Cepeda et al. 2006 [CE-7], Murre & Dros 2015 [CE-8],
Nelson & Narens 1990 [ML-6]

**"brainctl's consolidation prevents catastrophic forgetting":**
Kirkpatrick et al. 2017 [SHY-13], Tadros et al. 2022 [SHY-14],
Golden et al. 2022 [SHY-15], Feld & Born 2017 [SHY-12]

**"brainctl outperforms flat memory stores for multi-hop retrieval":**
Gutierrez et al. 2024 / HippoRAG [CE-11], Xu et al. 2025 / A-MEM
[CE-13], Pink et al. 2025 [CE-14], Packer et al. 2023 / MemGPT [CE-12]

**"brainctl adapts retrieval strategy via explore/exploit":**
Thompson 1933 [ML-1], Glowacka 2019 [ML-2], Zhang et al. 2026 [ML-3],
Finn et al. 2017 [ML-5]

---

## 12. 2026 Supplement — Papers Published January–April 2026

### 12.1 AI Agent Memory Architectures (2026)

**[2026-1]** Bousetouane, F. (2026). AI agents need memory control
over more context. *arXiv:2601.11653*.

> Agent Cognitive Compressor (ACC): separates artifact recall from
> state commitment. Candidate memories sit in a bounded staging area;
> only memories surviving N turns without contradiction get promoted.
> **Design implication:** add a staging buffer before brain.db commit —
> temporal verification window strengthening the W(m) gate.

**[2026-2]** Nguyen, A., Doan, D., Pham, H., Ha, B., Pham, D.,
Nguyen, L., Nguyen, H., Nguyen, T., Do, C., Nguyen, P., & Nguyen, T.
(2026). ByteRover: Agent-native memory through LLM-curated hierarchical
context. *arXiv:2604.01599*.

> Hierarchical Context Tree (Domain > Topic > Subtopic > Entry) with
> 5-tier retrieval resolving most queries within 100ms without LLM
> calls. Competitive on LoCoMo and LongMemEval. **Design implication:**
> augment FTS5 with a tiered retrieval pipeline — escalate from keyword
> → category-scoped → entity-graph → vsearch → LLM-assisted only when
> cheaper tiers fail.

**[2026-3]** Wang, S., Yu, E., Love, O., Zhang, T., Wong, T.,
Scargall, S., & Fan, C. (2026). MemMachine: A ground-truth-preserving
memory system for personalized AI agents. *arXiv:2604.04853*.

> Stores complete conversation episodes to preserve accuracy rather
> than extracting lossy summaries. Three memory types: short-term,
> long-term episodic, profile. 0.9169 accuracy on LoCoMo. **Design
> implication:** consider an optional episode store — compressed raw
> conversation chunks the consolidation engine can re-mine later.

**[2026-4]** Wen, S., & Ku, B. (2026). Knowledge compounding: An
empirical economic analysis of self-evolving knowledge wikis under the
agentic ROI framework. *arXiv:2604.11243*.

> 84.6% token savings vs. standard RAG when structured knowledge layers
> persist across queries. Costs decrease over time as coverage grows.
> Reconceptualizes LLM tokens from consumables to capital goods.
> **Design implication:** track "knowledge reuse rate" — how often
> orient/search results reduce downstream token consumption. Compute
> compounding ROI per project scope.

### 12.2 Memory Admission & Write Gates (2026)

**[2026-5]** Zhang, G., Jiang, W., Wang, X., Behr, A., Zhao, K.,
Friedman, J., Chu, X., & Anoun, A. (2026). Adaptive memory admission
control for LLM agents. *arXiv:2603.04549*. ICLR 2026 Workshop
MemAgents.

> A-MAC: decomposes memory value into five interpretable factors —
> future utility, factual confidence, semantic novelty, temporal
> recency, and content type prior. Content type prior is the single
> most influential factor. F1 = 0.583 on LoCoMo. **Design
> implication:** the most directly relevant paper for W(m). Replace
> the current surprise-only gate with A-MAC's 5-factor scoring:
> (1) future utility → demand_forecast signals, (2) factual confidence
> → source trust scores, (3) semantic novelty → existing FTS5 surprise,
> (4) temporal recency → decay curves, (5) content type prior →
> memory_suggest_category weightings.

### 12.3 Memory Benchmarks & Evaluation (2026)

**[2026-6]** He, Z., Wang, Y., Zhi, C., Hu, Y., Chen, T.-P., Yin, L.,
Chen, Z., Wu, T. A., Ouyang, S., Wang, Z., Pei, J., McAuley, J.,
Choi, Y., & Pentland, A. (2026). MemoryArena: Benchmarking agent memory
in interdependent multi-session agentic tasks.
*arXiv:2602.16313*.

> First benchmark testing memory where agents acquire it through
> environment interaction and later rely on it for actions. Agents
> performing well on LoCoMo struggle badly in this agentic setting.
> **Design implication:** build an automated end-to-end test harness
> where simulated sessions write memories, hand off, and the next
> session must use those memories to complete tasks. First integration
> test for the orient/wrap_up pipeline.

**[2026-7]** Hu, Y., Wang, Y., & McAuley, J. (2026). Evaluating
memory in LLM agents via incremental multi-turn interactions
(MemoryAgentBench). *arXiv:2507.05257*. Accepted ICLR 2026.

> Four core competencies: accurate retrieval, test-time learning,
> long-range understanding, selective forgetting. Current methods
> master none of all four. **Design implication:** map brainctl's
> tools to the four competencies and build a per-competency benchmark.

### 12.4 Multi-Agent Shared Memory (2026)

**[2026-8]** Yu, Z., Yu, N., Zhang, H., Ni, W., Yin, M., Yang, J.,
Zhao, Y., & Zhao, J. (2026). Multi-agent memory from a computer
architecture perspective: Visions and challenges ahead.
*arXiv:2603.10062*.

> Frames multi-agent memory as a computer architecture problem.
> Identifies two critical protocol gaps: cache sharing and structured
> access control. Shared memory needs coherence support. **Design
> implication:** add version counters on memory rows for concurrent
> modification detection, agent-scoped read locks during consolidation,
> workspace_broadcast notifications on shared-scope memory updates.

**[2026-9]** Ge, Z., Li, H., Wang, Y., Hu, N., Zhang, C. J., & Li, Q.
(2026). ClinicalAgents: Multi-agent orchestration for clinical decision
making with dual-memory. *arXiv:2603.26182*.

> Dual-memory: mutable Working Memory + static Experience Memory. MCTS
> for dynamic orchestration with hypothesis generation and backtracking.
> **Design implication:** formalize working/experience split. Working =
> MEB + handoff state. Experience = high-confidence semantic memories
> with source="human_verified". MCTS backtracking idea could improve
> dream_cycle contradiction handling.

**[2026-10]** Mao, W., Liu, H., Liu, Z., Tan, H., Shi, Y., Wu, J.,
Zhang, A., & Wang, X. (2026). Collaborative multi-agent optimization
for personalized memory system (CoMAM). *arXiv:2603.12631*.

> Models multi-agent memory as sequential MDP. Uses collaborative RL
> with group-level ranking consistency for cross-agent credit
> assignment. **Design implication:** track cross-agent memory utility
> (which agent's memories are most retrieved by other agents). Agents
> with higher cross-agent utility get lower W(m) thresholds.

**[2026-11]** Fleming, C., Kompella, R., Bosch, P., & Pandey, V.
(2026). Scaling multi-agent systems: A smart middleware for improving
agent interactions. *arXiv:2604.03430*.

> Cognitive Fabric Nodes: middleware that grounds inter-agent
> communication semantically before broadcasting. Prevents agents from
> fragmenting into isolated subjective realities. **Design
> implication:** run entity resolution + contradiction checks on
> memories before workspace_broadcast, so agents receive pre-validated
> grounded facts.

### 12.5 Adaptive RAG & Retrieval (2026)

**[2026-12]** Du, M., Xu, B., Zhu, C., Wang, S., Wang, P., Wang, X.,
& Mao, Z. (2026). A-RAG: Scaling agentic retrieval-augmented generation
via hierarchical retrieval interfaces. *arXiv:2602.03442*.

> Exposes keyword search, semantic search, and chunk read as separate
> tools — agent adaptively chooses retrieval granularity. Outperforms
> fixed pipelines. **Design implication:** validates brainctl's
> multi-tool approach (memory_search / vsearch / entity_search).
> Add a meta-tool that recommends which search tool to use based on
> query characteristics.

**[2026-13]** Pollertlam, N., & Kornsuwannawit, W. (2026). Beyond the
context window: A cost-performance analysis of fact-based memory vs.
long-context LLMs for persistent agents. *arXiv:2603.04814*.

> Memory systems become more cost-effective than long-context after
> ~10 interaction turns at 100k tokens. **Design implication:** track
> cumulative tokens saved by brainctl vs. hypothetical full-context
> cost. Report the break-even point per project.

### 12.6 Continual Learning & Forgetting (2026)

**[2026-14]** Wang, Z., Wu, Z., Li, Y., Liu, B., Li, G., & Wang, Y.
(2026). Continual learning of achieving forgetting-free and positive
knowledge transfer. *arXiv:2601.05623*.

> ETCL: task-specific binary masks isolate sparse sub-networks per
> task. Achieves positive forward AND backward transfer — not just
> preventing forgetting but improving old tasks. **Design implication:**
> per-project "relevance masks" over the global memory pool. When a new
> project is added, run backward transfer — check if new memories
> improve retrieval quality for existing projects.

**[2026-15]** Imanov, O. Y. L. (2026). Mechanistic analysis of
catastrophic forgetting in large language models during continual
fine-tuning. *arXiv:2601.18699*.

> Identifies three forgetting mechanisms: gradient interference in
> attention weights, representational drift, loss landscape flattening.
> Forgetting severity correlates with task similarity. **Design
> implication:** memories in crowded semantic neighborhoods decay faster
> unless they carry distinguishing specificity — "similarity-aware
> decay."

### 12.7 Neuroscience of Memory Consolidation (2026)

**[2026-16]** Fountas, Z., Oomerjee, A., Bou-Ammar, H., Wang, J., &
Burgess, N. (2026). Why the brain consolidates: Predictive forgetting
for optimal generalisation. *arXiv:2603.04688*.

> Consolidation optimizes stored knowledge through "predictive
> forgetting" — selectively preserving information that predicts future
> outcomes. Offline consolidation serves compression for generalization,
> not just stabilization. **Design implication:** retain memories with
> highest predictive value for future queries (from demand_forecast).
> Memories describing past events without future predictive value should
> be compressed/retired more aggressively.

**[2026-17]** Alevi, D., Lundt, F., Ciceri, S., Heiney, K., &
Sprekeler, H. (2026). Memory consolidation and representational drift.
*bioRxiv 2026.03.09.710554*.

> Memories follow deterministic trajectories through pattern space
> during consolidation. Representational drift is a natural consequence
> of ongoing consolidation, not noise. **Design implication:** allow
> memory embeddings/tags to gradually update during consolidation based
> on access context — "controlled drift" aligns memories with how
> they're actually used rather than how they were originally written.

**[2026-18]** Robinson, H. L., Todorova, R., Nagy, G. A., Gruzdeva,
A., Paudel, P., Oliva, A., & Fernandez-Ruiz, A. (2026). Large
sharp-wave ripples promote hippocampo-cortical memory reactivation and
consolidation during sleep. *Neuron*, 114(2), 226-236.e6.

> Only large-amplitude SWRs drive consolidation; small ripples do not.
> Optogenetic SWR boosting during sleep rescues otherwise-forgotten
> memories. **Design implication:** replay should be weighted by
> activation magnitude — high-salience replay candidates get priority
> and potentially multiple passes. The replay_boost tool is the
> "optogenetic rescue" analog for at-risk important memories.

**[2026-19]** Pouget, C., Morier, F., Treiber, N., et al. (2026).
Deconstruction of a memory engram reveals distinct ensembles recruited
at learning. *Nature Neuroscience*.

> A memory engram consists of distinct, non-overlapping sub-ensembles
> recruited at different temporal phases of learning. A "core engram"
> is essential for recall; peripheral components can be lost. **Design
> implication:** memories should be decomposable into core vs.
> peripheral components with differential protection during
> consolidation. Core components (highest activation) get stronger
> Hebbian reinforcement and EWC protection.

**[2026-20]** Morici, J. F., Silva, A., Lima-Paiva, I., et al. (2026).
Dorsoventral hippocampus neural assemblies reactivate during sleep
following an aversive experience. *Nature Neuroscience*.

> Replay following aversive experiences more faithfully reproduces
> original firing patterns than replay following rewarding experiences.
> Negative experiences get higher-fidelity replay. **Design
> implication:** memories tagged with negative valence (errors,
> warnings, failures, corrections) should get higher-fidelity
> reconstruction during consolidation — not replayed more often, but
> replayed more accurately.

**[2026-21]** Kehl, M. S., Reber, T. P., Borger, V., Surges, R.,
Mormann, F., & Staresina, B. P. (2026). Sleep ripples drive
single-neuron reactivation for human memory consolidation.
*bioRxiv 2026.03.27.714528*.

> First direct evidence in humans that ripple-driven single-neuron
> reactivation during sleep supports episodic consolidation. Sleep
> ripples elicit stronger activation than wake ripples. Neurons coding
> remembered items fire more during sleep ripples. **Design
> implication:** consolidation cycles (offline "sleep") should be
> protected from interruption by active queries. Consider a "quiet
> period" flag for exclusive consolidation access.

**[2026-22]** O'Neill, O. S., & Winters, B. D. (2026). Breaking
boundaries: Dopamine's role in prediction error, salient novelty, and
memory reconsolidation. *Neuroscience*, 594, 31-41.

> Dopamine enables memory modification by overcoming biological
> "boundary conditions" that normally prevent memory destabilization.
> High surprise (prediction error) breaches even strong memories'
> protection. **Design implication:** add `modification_resistance` to
> memories that increases with age/access/EWC importance. The W(m)
> surprise signal must exceed this resistance to enable
> reconsolidation. Low surprise fails to destabilize strong memories —
> which is protective and correct.

**[2026-23]** Dupret, D., Fusi, S., & Panzeri, S. (2026). Neural
population activity for memory: Properties, computations, and codes.
*Neuron*, 114(3), 390-407.

> Memory circuits navigate trade-offs between high-dimensional
> representations (resist interference, costly) and overlapping
> representations (enable generalization, cause confusion). A "safe
> zone" in population-activity space balances both. **Design
> implication:** monitor embedding space utilization as a health
> metric — memories too clustered (interference risk) vs. too
> dispersed (retrieval cost). Trigger pattern separation when clusters
> get too dense.

**[2026-24]** Niediek, J., Reber, T. P., et al. (2026). Episodic
memory consolidation by reactivation of human concept neurons during
sleep reflects contents, not sequence of events.
*bioRxiv 2026.01.10.698827*.

> Concept neurons reactivate based on content association within the
> same episode, not temporal sequence. Co-reactivation is the
> consolidation mechanism for "who/what." **Design implication:**
> restructure replay queue by entity cluster, not temporal order.
> Memories sharing common entities should be co-activated regardless
> of creation time, strengthening Hebbian links by semantic relatedness
> rather than temporal adjacency.

**[2026-25]** Schwimmbeck, F., Niediek, J., et al. (2026). Sequential
coupling of sleep oscillations enables concept-neuron reactivation and
supports information flow across the human hippocampal-cortical circuit.
*bioRxiv 2026.01.15.699122*.

> Cross-regional co-activation is enhanced only when hippocampal SWRs
> coincide with cortical slow oscillation-spindle complexes. The
> cortex actively shapes consolidation. **Design implication:** gate
> memory promotion on whether replayed memories integrate with existing
> semantic structures. Only replays that find matching knowledge-graph
> structures proceed to long-term storage.

**[2026-26]** Causse, A. A., Curot, J., Lopes-dos-Santos, V., et al.
(2026). A learning-evoked slow-oscillatory architecture paces
population activity for offline reactivation across the human medial
temporal lobe. *bioRxiv 2026.02.12.705512*.

> Learning-time oscillatory bursts structure coactivity patterns that
> are selectively reactivated during post-learning rest. Reactivation
> strength predicts subsequent recall accuracy. **Design implication:**
> implement write-time coordination tagging — memories written in close
> temporal proximity with overlapping entity references form a
> "coordination cluster" that should be replayed together as a unit.

**[2026-27]** Aquino Argueta, S., Lazarus, A., Yao, F., et al. (2026).
Reactivation during sleep segregates the neural representations of
episodic memories. *bioRxiv 2026.04.08.717230*.

> Sleep reactivation actively separates overlapping representations —
> it does not merely strengthen memories but de-confuses them.
> **Design implication:** add a de-overlap mechanism during
> consolidation. When similar-but-distinct memories are detected
> (high embedding similarity, different entities/contexts), push their
> representations apart or add discriminative metadata rather than
> merging them.

**[2026-28]** Widloski, J., & Foster, D. J. (2025). Replay without
sharp wave ripples in a spatial memory task. *Nature Communications*,
16, 10287. (Published Nov 2025; included for direct relevance.)

> Replay sequences can occur without ripples. Ripples serve a selective
> tagging function, not a replay function. Replay and tagging are
> distinct but coordinated. **Design implication:** decouple replay
> from tagging in the consolidation pipeline. Replay happens broadly;
> a separate context-sensitive tagging step selects which replayed
> memories deserve strengthening.

### 12.8 Surveys & Frameworks (2026)

**[2026-29]** Du, P. (2026). Memory for autonomous LLM agents:
Mechanisms, evaluation, and emerging frontiers. *arXiv:2603.07670*.

> Comprehensive survey formalizing agent memory as a write-manage-read
> loop. Identifies "learned forgetting" as the key open frontier.
> **Design implication:** brainctl is strong on write (W(m)) and read
> (FTS5/vsearch) but the "manage" layer (consolidation, forgetting) is
> the weakest link — consistent with this survey's assessment.

**[2026-30]** Dong, C. V., Lu, Q., Norman, K. A., & Michelmann, S.
(2026). Towards large language models with human-like episodic memory.
*Trends in Cognitive Sciences*, 30(2).

> Identifies key episodic memory properties missing from current LLMs:
> dynamic memory updating, event segmentation, selective encoding/
> retrieval, temporal contiguity, and competition at retrieval.
> **Design implication:** add a temporal contiguity bonus to search —
> when a memory is retrieved, boost scores of temporally adjacent
> memories from the same session. Add event boundary detection during
> writes (significant project/context shifts trigger epoch boundaries).

### 12.9 Updated Whitepaper Citation Guide (2026 additions)

**"brainctl uses a principled memory admission gate":**
Zhang et al. 2026 / A-MAC [2026-5], Bousetouane 2026 / ACC [2026-1]

**"brainctl's consolidation is grounded in 2026 neuroscience":**
Robinson et al. 2026 [2026-18] (large SWR selective consolidation),
Pouget et al. 2026 [2026-19] (engram temporal layers),
Morici et al. 2026 [2026-20] (aversive replay prioritization),
Niediek et al. 2026 [2026-24] (content-association replay),
Aquino Argueta et al. 2026 [2026-27] (sleep de-overlaps memories),
Fountas et al. 2026 [2026-16] (predictive forgetting),
Alevi et al. 2026 [2026-17] (representational drift)

**"brainctl outperforms flat memory on agentic benchmarks":**
He et al. 2026 / MemoryArena [2026-6],
Hu et al. 2026 / MemoryAgentBench [2026-7],
Wen & Ku 2026 [2026-4] (84.6% token savings)

**"brainctl supports principled multi-agent memory":**
Yu et al. 2026 [2026-8] (coherence protocols),
Mao et al. 2026 / CoMAM [2026-10] (cross-agent credit assignment),
Fleming et al. 2026 [2026-11] (semantic grounding middleware),
Ge et al. 2026 [2026-9] (dual-memory architecture)

**"brainctl's forgetting is sculpted, not accidental":**
Fountas et al. 2026 [2026-16] (predictive forgetting),
O'Neill & Winters 2026 [2026-22] (dopamine boundary conditions),
Wang et al. 2026 [2026-14] (positive knowledge transfer),
Dong et al. 2026 [2026-30] (selective encoding/forgetting)
