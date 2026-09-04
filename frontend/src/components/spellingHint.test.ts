import { describe, expect, it } from 'vitest';
import type { StudyCard } from '../api';
import { buildSpellingHint } from './spellingHint';

function makeCard(overrides: Partial<StudyCard>): StudyCard {
  return {
    cardId: 'card-1',
    cardIds: ['card-1'],
    word: 'hydrogen',
    partOfSpeech: 'noun',
    senseLabel: 'chemical element',
    definition: 'a chemical element',
    definitionSource: 'oxford_api',
    examples: [],
    chineseNote: null,
    senses: [],
    queueType: 'new',
    degraded: false,
    ...overrides
  };
}

describe('buildSpellingHint (F-01: prompt must not leak the answer)', () => {
  it('drops the sentence containing the answer and keeps the first safe one (hydrogen)', () => {
    const hint = buildSpellingHint(makeCard({
      definition: 'a chemical element. Hydrogen is a gas that is the lightest of all the elements.'
    }));

    expect(hint).toBe('a chemical element');
    expect(hint).not.toMatch(/hydrogen/i);
  });

  it('matches the answer case-insensitively (HYDROGEN in the definition)', () => {
    const hint = buildSpellingHint(makeCard({
      definition: 'HYDROGEN is the lightest gas. A chemical element.'
    }));

    expect(hint).toBe('A chemical element');
  });

  it('falls back to the structured hint when every sentence leaks the answer', () => {
    const hint = buildSpellingHint(makeCard({
      word: 'gulf',
      definition: 'The gulf widens. A gulf is an arm of the sea. The Persian Gulf is famous.'
    }));

    expect(hint).toBe('4 letters · starts with "g" · ends with "f"');
    expect(hint).not.toMatch(/gulf/i);
  });

  it('masks the structured hint when its own boilerplate would leak the answer', () => {
    expect(buildSpellingHint(makeCard({
      word: 'letter',
      definition: 'Letters are written messages. People write letters to each other.'
    }))).toBe('l _ _ _ _ r');

    expect(buildSpellingHint(makeCard({
      word: 'start',
      definition: 'to start something. The start of an event.'
    }))).toBe('s _ _ _ t');
  });

  it('catches inflected forms (studies for study, loving for love)', () => {
    expect(buildSpellingHint(makeCard({
      word: 'study',
      definition: 'He studies hard. A room used for reading and writing.'
    }))).toBe('A room used for reading and writing');

    expect(buildSpellingHint(makeCard({
      word: 'love',
      definition: 'Loving someone is intense. A strong feeling of affection.'
    }))).toBe('A strong feeling of affection');
  });

  it('treats any component word of a multi-word answer as a leak', () => {
    const hint = buildSpellingHint(makeCard({
      word: 'carbon dioxide',
      definition: 'a gas produced when carbon burns'
    }));

    expect(hint).toBe('2 words · 13 letters · starts with "c" · ends with "e"');
    expect(hint).not.toMatch(/carbon/i);
  });

  it('matches short answer tokens as whole words but not as prefixes', () => {
    // "El Nino": "el" must match the word El, but must not match "element".
    expect(buildSpellingHint(makeCard({
      word: 'El Nino',
      chineseNote: 'El Nino phenomenon',
      definition: 'an irregular weather pattern in the Pacific'
    }))).toBe('an irregular weather pattern in the Pacific');
  });

  it('catches possessive forms (hydrogen\'s)', () => {
    expect(buildSpellingHint(makeCard({
      definition: "Hydrogen's boiling point is tiny. A chemical element."
    }))).toBe('A chemical element');
  });

  it('prefers a safe chinese note over the definition', () => {
    expect(buildSpellingHint(makeCard({
      chineseNote: '氢气，最轻的气体元素',
      definition: 'Hydrogen is a gas.'
    }))).toBe('氢气，最轻的气体元素');
  });

  it('skips a leaking chinese note and falls through to the definition', () => {
    expect(buildSpellingHint(makeCard({
      word: 'El Nino',
      chineseNote: 'El Nino phenomenon',
      definition: 'a weather pattern that warms the eastern Pacific Ocean'
    }))).toBe('a weather pattern that warms the eastern Pacific Ocean');
  });

  it('falls through card-level candidates to the first sense', () => {
    const sense = {
      cardId: 'card-1',
      partOfSpeech: 'noun',
      senseLabel: 'chemical element',
      definition: 'Hydrogen is a gas. A chemical element.',
      definitionSource: 'oxford_api' as const,
      examples: [],
      chineseNote: null
    };

    expect(buildSpellingHint(makeCard({
      definition: 'Hydrogen is the lightest element.',
      senses: [sense]
    }))).toBe('A chemical element');
  });

  it('keeps a safe definition when the answer appears nowhere in it', () => {
    expect(buildSpellingHint(makeCard({
      definition: 'a room used for reading and writing'
    }))).toBe('a room used for reading and writing');
  });
});
