// The recorded question runs the tests replay (tests/fixtures/question/<sample>/events.json,
// built by `tests/py/replay.py --events`).
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { QuestionEvent } from '../question/types';

export function recordedLog(sample = 'radar-nowcasting-dl'): QuestionEvent[] {
  const path = resolve(__dirname, `../../../tests/fixtures/question/${sample}/events.json`);
  return JSON.parse(readFileSync(path, 'utf8')) as QuestionEvent[];
}
