import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
const sdkHome = process.env.JEVFLOW_GATEWAY_HOME || join(homedir(), '.local/share/jevflow/ai-gateway');
const { experimental_evaluate: evaluate } = createRequire(join(sdkHome, 'package.json'))('ai');

const model = 'typesafe-ai/jev';
const smokeRequest = {
  state: '玩家要求加入斗地主对局。',
  questions: {
    intent: {
      type: 'choice',
      instructions: '玩家希望进行什么操作？',
      criteria: {
        join: '加入对局。',
        leave: '退出对局。',
        other: '其他操作。',
      },
    },
  },
};

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1 || args[0] === '--help') {
    console.log('Usage: node adapters/ai-gateway/evaluate.mjs --smoke | request.json | -');
    console.log('Requires AI_GATEWAY_API_KEY. Use - to read JSON from stdin.');
    if (args[0] !== '--help') process.exitCode = 1;
    return;
  }
  if (!process.env.AI_GATEWAY_API_KEY?.trim()) {
    throw new Error('Set AI_GATEWAY_API_KEY in the process environment.');
  }

  const smoke = args[0] === '--smoke';
  let request = smokeRequest;
  if (!smoke) {
    const input = args[0] === '-'
      ? await readFile('/dev/stdin', 'utf8')
      : await readFile(args[0], 'utf8');
    try {
      request = JSON.parse(input);
    } catch {
      throw new Error('Input must be valid JSON.');
    }
  }
  if (!request || !Object.hasOwn(request, 'state') || !request.questions
      || typeof request.questions !== 'object' || Array.isArray(request.questions)
      || Object.keys(request.questions).length === 0) {
    throw new Error('Input requires state and a non-empty questions object.');
  }

  const bridgeStarted = performance.now();
  const deadline = request.deadlineUnixMs ?? (Date.now() + 30_000);
  if (!Number.isFinite(deadline)) throw new Error("Invalid request deadline.");
  const remaining = Math.floor(deadline - Date.now());
  if (remaining <= 0) {
    console.log(JSON.stringify({ok: false, error: {code: 'timeout', httpStatus: null},
      timing: {sdkMs: 0, bridgeMs: Math.round(performance.now() - bridgeStarted)}}));
    process.exitCode = 1;
    return;
  }
  const signal = AbortSignal.timeout(remaining);
  const started = performance.now();
  let result;
  try {
    result = await evaluate({
      model,
      state: request.state,
      questions: request.questions,
      maxRetries: 0,
      abortSignal: signal,
    });
  } catch (error) {
    const status = Number.isInteger(error.statusCode) ? error.statusCode : null;
    console.log(JSON.stringify({ok: false, error: {
      code: signal.aborted ? 'timeout' : status ? 'http' : 'network', httpStatus: status,
    }, timing: {sdkMs: Math.round(performance.now() - started), bridgeMs: Math.round(performance.now() - bridgeStarted)}}));
    process.exitCode = 1;
    return;
  }
  if (smoke && result.answers.intent?.choice !== 'join') {
    throw new Error('Jev responded, but the smoke check did not select join.');
  }
  console.log(JSON.stringify({
    model,
    timing: {sdkMs: Math.round(performance.now() - started), bridgeMs: Math.round(performance.now() - bridgeStarted)},
    elapsedMs: Math.round(performance.now() - started),
    answers: result.answers,
    confidence: result.providerMetadata?.typesafe?.confidence,
    usage: result.usage,
  }, null, 2));
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
