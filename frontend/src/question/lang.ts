// The language of text the console did not write (a question, a model's plan, a
// paper title): Chinese when it has Han characters, else English. It is marked on
// its element, so a Chinese question in the English console is not read as English.
// Two values on purpose: the console speaks zh and en, and text in a third language
// (Spanish, kana) is marked en rather than taking the console's zh-CN.
import type { Lang } from './types';

const HAN = /[㐀-鿿豈-﫿]/;

export function textLang(text: string): Lang {
  return HAN.test(text) ? 'zh' : 'en';
}
