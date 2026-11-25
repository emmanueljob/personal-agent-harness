'use strict';
(() => {
  const byId = (id) => document.getElementById(id),
    write = (id, value) => (byId(id).textContent = value);
  const methods = {
    prompt: [
      'Prompting and a few examples',
      'Keep the model’s weights unchanged. Supply instructions, an intent, and a few examples of the desired writing at request time.',
      'Fast iteration, transparent examples, and no training pipeline. It is the baseline a voice adapter should beat.',
      'Examples consume context, may need to be repeated, and do not guarantee stable behavior across tasks.',
      'We should have included a stronger few-shot comparison. The base model already expresses several supplied intentions more clearly than our adapter.',
    ],
    rag: [
      'Retrieve the facts when they are needed',
      'Look up source records and give the relevant evidence to the model with the current request.',
      'Facts can be corrected or refreshed without retraining weights. The answer can cite the source.',
      'Retrieval can miss the right record, return irrelevant material, or fill the context with noise. Good retrieval timing and budgets still require design.',
      'This is a strong candidate for current personal knowledge. It does not, by itself, teach your voice or solve the routing problem.',
    ],
    lora: [
      'LoRA with a higher-precision base',
      'Freeze a floating-point base and train small low-rank adapter matrices.',
      'The adapter remains compact, while the base avoids this 4-bit approximation. It is a useful comparison for diagnosing precision effects.',
      'The base occupies more memory. Activations and optimizer state still add to training use. Better writing is not guaranteed.',
      'A plausible alternative on this Mac. We have not run the matched comparison, so we cannot say our failures would disappear.',
    ],
    qlora: [
      'Quantized LoRA: the path we used',
      'Freeze the compact 4-bit base and train floating-point adapter parameters on owner-authored examples.',
      'A small local footprint, short iteration cycles, a roughly 10 MB adapter, and rollback by unloading that adapter.',
      'The base is approximate; the update is constrained; adapters require compatible weights and runtime support. Output quality still needs direct evaluation.',
      'A good learning vehicle. Our results do not yet establish that this is the best method for a useful personal writing tool.',
    ],
    full: [
      'Full fine-tuning',
      'Allow the base model’s trainable parameters to update, rather than restricting the learned change to a small adapter.',
      'More freedom to change behavior when a well-defined task and representative data justify it.',
      'Much larger gradient and optimizer storage, more operational expense, and greater opportunity to degrade existing capabilities on narrow data.',
      'Not justified by 21 writing examples. We would first ask whether a simpler method meets the task.',
    ],
    distill: [
      'Use a teacher to produce training signals',
      'Have a stronger teacher supply outputs or target distributions for a student. This changes the source of supervision; the student may still use LoRA or QLoRA.',
      'Can expand coverage or transfer behavior for a precisely defined task when teacher outputs are carefully evaluated.',
      'The teacher can introduce errors and its own style. Generated text is not automatically evidence of your authentic voice. Data provenance and use terms need to be understood.',
      'Our actual targets are your original words. A future teacher could help propose scenarios or annotations, but synthetic writing should be labeled and reviewed.',
    ],
    preference: [
      'Learn from preferred and rejected responses',
      'Collect reliable comparisons, then choose a preference-optimization method. Some approaches use RL; DPO provides another route.',
      'Can target qualities that are hard to express as a single reference reply, such as which draft feels more natural.',
      'Preferences must be consistent and sufficiently representative. A handful of clicks is not enough to establish a reliable reward or preference dataset.',
      'The website collects feedback only. We would first use corrections and ratings to understand errors, not launch an RL loop automatically.',
    ],
  };
  function method() {
    const x = methods[byId('method-choice').value];
    [
      'method-title',
      'method-purpose',
      'method-gain',
      'method-cost',
      'method-verdict',
    ].forEach((id, i) => write(id, x[i]));
  }
  function quant() {
    const bits = Number(byId('quant-bits').value),
      levels = 2 ** bits,
      original = Number(byId('quant-weight').value),
      lo = -1,
      hi = byId('quant-outlier').checked ? 6 : 1,
      scale = (hi - lo) / (levels - 1),
      code = Math.min(
        levels - 1,
        Math.max(0, Math.round((original - lo) / scale)),
      ),
      approx = lo + code * scale,
      error = Math.abs(original - approx);
    write('quant-original', original.toFixed(6));
    write('quant-code', code);
    write('quant-value', approx.toFixed(6));
    write('quant-error', error.toFixed(6));
    write('quant-scale', scale.toFixed(6));
    write(
      'quant-explanation',
      `${levels.toLocaleString()} codes cover ${lo} to +${hi}. Code ${code} reconstructs as ${scale.toFixed(6)} × ${code} − 1 ≈ ${approx.toFixed(6)}. ${hi === 6 ? 'The outlier widens the shared range, so adjacent levels are farther apart.' : 'Nearby numbers share a code when they fall in the same rounding interval.'}`,
    );
    const x = (v) => 48 + ((v - lo) / (hi - lo)) * 684;
    let svg = `<line x1="48" y1="65" x2="732" y2="65" stroke="#adbbd1" stroke-width="3"/>`;
    if (levels <= 256) {
      for (let i = 0; i < levels; i++) {
        const px = x(lo + i * scale);
        svg += `<line x1="${px}" y1="58" x2="${px}" y2="72" stroke="#8e9db6" stroke-width="${levels === 16 ? 2 : 0.6}"/>`;
      }
    }
    svg += `<text x="48" y="102" fill="#5f6d81" font-size="16">−1</text><text x="732" y="102" text-anchor="end" fill="#5f6d81" font-size="16">+${hi}</text><circle cx="${x(original)}" cy="43" r="7" fill="#4554d4"/><line x1="${x(original)}" y1="50" x2="${x(original)}" y2="65" stroke="#4554d4"/><circle cx="${x(approx)}" cy="84" r="7" fill="#147b72"/><line x1="${x(approx)}" y1="65" x2="${x(approx)}" y2="77" stroke="#147b72"/><text x="48" y="22" fill="#4554d4" font-size="16">Original (above)</text><text x="732" y="130" text-anchor="end" fill="#147b72" font-size="16">Reconstructed (below)</text>`;
    byId('quant-grid').innerHTML = svg;
    byId('quant-grid').setAttribute(
      'aria-label',
      `Original ${original.toFixed(6)}, reconstructed ${approx.toFixed(6)}, absolute error ${error.toFixed(6)} using ${bits} bits.`,
    );
  }
  function memory() {
    const count = Number(byId('memory-model').value) * 1e9,
      bits = Number(byId('memory-bits').value),
      group = Number(byId('memory-group').value),
      payload = (count * bits) / 8 / 1e9,
      metadata = bits === 16 ? 0 : ((count / group) * 4) / 1e9;
    byId('memory-group').disabled = bits === 16;
    write('memory-payload', `${payload.toFixed(3)} GB`);
    write('memory-overhead', `${metadata.toFixed(3)} GB`);
    write('memory-total', `${(payload + metadata).toFixed(3)} GB`);
  }
  function rank() {
    const r = [2, 4, 8, 16, 32, 64, 128][Number(byId('rank-slider').value)],
      n = 2048 * 2 * r;
    write('rank-label', r);
    byId('rank-slider').setAttribute('aria-valuetext', `Rank ${r}`);
    write('rank-shapes', `2,048 × ${r} and ${r} × 2,048`);
    write('rank-count', `${n.toLocaleString()} trainable parameters`);
    write(
      'rank-explanation',
      `Rank ${r} uses ${((n / (2048 * 2048)) * 100).toFixed(3)}% as many trainable numbers as a full update to this one matrix. Increasing rank adds capacity and storage; it does not establish that the task needs that capacity.`,
    );
  }
  const loops = [
    [
      'INPUT',
      'Read the context and the correct reply',
      'The tokenizer turns instructions, the intent or conversation context, and the target reply into IDs. The prompt tokens are visible, but only the reply and end-of-message tokens are scored. Our maximum sequence length is 1,536 tokens; the preparation code checks that examples fit rather than silently cutting off targets.',
    ],
    [
      'FORWARD PASS',
      'Predict the next target tokens',
      'The quantized base and floating-point adapter process the sequence. At each target position, the model produces scores across its vocabulary. Those scores define probabilities for possible next tokens. During this calculation, earlier target tokens are supplied correctly.',
    ],
    [
      'LOSS',
      'Measure how surprising the real words were',
      'A target token assigned little probability produces a larger negative-log-probability loss. Assigning it more probability lowers that loss. The training objective does not directly ask whether the message is truthful, sounds like you, or would be useful to send.',
    ],
    [
      'BACKWARD PASS',
      'Trace how adapter parameters affected the loss',
      'Automatic differentiation computes the relevant gradients through the model’s computation. The base remains frozen; the adapter parameters are the variables we allow the optimizer to change. Frozen weights still participate in the forward computation.',
    ],
    [
      'OPTIMIZER STEP',
      'Adjust the adapter',
      'Adam uses the gradients and its running statistics to update adapter parameters. Our learning rate is 0.0001 and our batch size is one example. The packed base weights do not change. This is one training step—not one entire conversation learned permanently.',
    ],
    [
      'PERIODIC EVALUATION',
      'Check examples that did not drive updates',
      'At recorded checkpoints, we compute validation loss without optimizing on those examples. We keep the trained checkpoint with the lowest observed validation loss. Test scores are reported separately. Validation is periodic, not necessarily after every update.',
    ],
  ];
  function loop(index) {
    const x = loops[index];
    write('loop-label', x[0]);
    write('loop-title', x[1]);
    write('loop-body', x[2]);
    document.querySelectorAll('[data-loop]').forEach((b) => {
      const active = Number(b.dataset.loop) === index;
      b.classList.toggle('selected', active);
      b.setAttribute('aria-pressed', String(active));
    });
  }
  const stages = {
    data: [
      'A narrow, selected sample',
      '43 original targets in the first pass; 21 in the curated pass. The curation deliberately emphasizes curious product thinking. That is a useful direction, but it is still a small slice of the owner’s writing.',
      'Representative, attributable examples',
      'Define the intended audiences and situations. Collect examples across those situations, deduplicate them, preserve provenance, label synthetic material, and inspect coverage before expanding volume.',
    ],
    eval: [
      'A diagnostic, not a reliability claim',
      'Four or eight held-out examples, one seed, validation-based checkpoint selection, and a handful of recorded generation probes. The two passes change both the task and data.',
      'A stable evaluation contract',
      'Reserve fresh conversations and scenarios. Compare prompt-only and trained baselines with consistent prompts and decoding. Score voice, intent, facts, repetition, latency, and uncertainty; select configurations on validation, then report the held-out result.',
    ],
    runtime: [
      'A local lab and a recorded website',
      'Training ran on the Mac. The public tutorial uses simulations to explain the mechanics. The adapter has not been integrated into the application.',
      'A versioned, observable release',
      'Test the exact serving artifact and workload. Track base, adapter, tokenizer, and configuration versions together; establish latency and memory budgets; keep rollback and a baseline fallback; start with human-reviewed drafts.',
    ],
    memory: [
      'Visible notes, limited recall testing',
      'We extracted six source-backed notes. A first-pass recall probe failed to reliably recover a stated preference. The curated experiment evaluates supplied-intent writing, not personal-fact recall.',
      'Evidence with evaluated retrieval',
      'Maintain correctable, dated records. Test whether retrieval finds the right source at the right time, how much context it uses, and whether the final answer stays faithful. Evaluate routing separately from writing style.',
    ],
  };
  function production() {
    const x = stages[byId('production-lens').value];
    ['toy-title', 'toy-body', 'real-title', 'real-body'].forEach((id, i) =>
      write(id, x[i]),
    );
  }
  byId('method-choice').addEventListener('change', method);
  ['quant-bits', 'quant-outlier'].forEach((id) =>
    byId(id).addEventListener('change', quant),
  );
  byId('quant-weight').addEventListener('input', quant);
  ['memory-model', 'memory-bits', 'memory-group'].forEach((id) =>
    byId(id).addEventListener('change', memory),
  );
  byId('rank-slider').addEventListener('input', rank);
  byId('production-lens').addEventListener('change', production);
  document
    .querySelectorAll('[data-loop]')
    .forEach((b) =>
      b.addEventListener('click', () => loop(Number(b.dataset.loop))),
    );
  method();
  quant();
  memory();
  rank();
  loop(0);
  production();
})();
