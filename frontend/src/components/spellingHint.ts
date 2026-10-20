import type { StudyCard } from '../api';

// F-01 (P0, v1.1): the spelling prompt must never contain the answer word
// in any casing or common inflected form. A sentence that leaks the answer
// is unusable as a whole; when no candidate sentence survives we fall back
// to a structured hint (letter count + first/last letter) so the prompt
// keeps real coaching value instead of degrading to "no hint".
//
// Word-form boundary: a text word leaks when it equals a short answer
// token (2 chars) or starts with a longer answer token / a simple
// inflection stem of one (trailing y -> i, trailing e dropped). That
// covers the suffix families (hydrogen, hydrogens, hydrogen's; study,
// studies, studied; love, loving) but not irregular stems (tooth/teeth,
// swim/swam); when in doubt the check errs on the conservative side —
// a false positive only downgrades the hint, never leaks the answer.

const SENTENCE_SPLIT = /[.!?;。！？；]+/;

function normalizeWord(word: string): string {
  return word.replace(/[‘’]/g, "'").trim().toLowerCase();
}

function answerTokens(word: string): string[] {
  return normalizeWord(word)
    .split(/[^a-z0-9']+/)
    .map((token) => token.replace(/^'+|'+$/g, ''))
    .filter((token) => token.length >= 2);
}

function inflectionStems(token: string): string[] {
  const stems = [token];
  if (token.length >= 3 && token.endsWith('y')) {
    stems.push(`${token.slice(0, -1)}i`);
  }
  if (token.length >= 4 && token.endsWith('e')) {
    stems.push(token.slice(0, -1));
  }
  return stems;
}

function textWordLeaks(textWord: string, token: string): boolean {
  if (token.length < 3) {
    return textWord === token;
  }
  return inflectionStems(token).some((stem) => textWord.startsWith(stem));
}

function textLeaksAnswer(text: string, tokens: string[]): boolean {
  const textWords = normalizeWord(text)
    .split(/[^a-z0-9']+/)
    .map((word) => word.replace(/^'+|'+$/g, ''));
  return tokens.some((token) =>
    textWords.some((textWord) => textWord.length > 0 && textWordLeaks(textWord, token))
  );
}

function firstSafeSentence(text: string, tokens: string[]): string | null {
  for (const sentence of text.split(SENTENCE_SPLIT)) {
    const trimmed = sentence.trim();
    if (trimmed.length > 0 && !textLeaksAnswer(trimmed, tokens)) {
      return trimmed;
    }
  }
  return null;
}

function structuredHint(word: string, tokens: string[]): string {
  const clean = word.replace(/[‘’]/g, "'").trim();
  const letterCount = clean.replace(/[^A-Za-z0-9]/g, '').length;
  const firstLetter = clean.charAt(0).toLowerCase();
  const lastLetter = clean.charAt(clean.length - 1).toLowerCase();
  const wordCount = clean.split(/\s+/).filter(Boolean).length;
  const shape = `${letterCount} letters · starts with "${firstLetter}" · ends with "${lastLetter}"`;
  const hint = wordCount <= 1 ? shape : `${wordCount} words · ${shape}`;
  // The boilerplate itself ("letters", "starts with", ...) can contain the
  // answer when the answer IS one of those words (letter, star, start...).
  // Fall back to a word-free letter mask so the hint still never leaks.
  if (tokens.length > 0 && textLeaksAnswer(hint, tokens)) {
    return maskHint(clean);
  }
  return hint;
}

function maskHint(word: string): string {
  return word
    .split(/\s+/)
    .filter(Boolean)
    .map((wordPart) => {
      const letters = Array.from(wordPart).filter((char) => /[A-Za-z0-9]/.test(char));
      if (letters.length <= 2) {
        return wordPart;
      }
      return `${letters[0]}${' _'.repeat(letters.length - 2)} ${letters[letters.length - 1]}`;
    })
    .join(' ');
}

export function buildSpellingHint(card: StudyCard): string {
  const tokens = answerTokens(card.word);
  const candidates = [
    card.chineseNote,
    card.senses[0]?.chineseNote ?? null,
    card.definition,
    card.senses[0]?.definition ?? null
  ];

  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }
    const sentence = firstSafeSentence(candidate, tokens);
    if (sentence) {
      return sentence;
    }
  }

  return structuredHint(card.word, tokens);
}
