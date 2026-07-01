# VocabularyLearning Context

## Glossary

### Local-First Web App

A personal study application that is opened in a browser on `localhost` while keeping study data on the user's own machine.

It does not require accounts, cloud sync, or a remote service for core study workflows.

### Manual Word Intake

The learner adds vocabulary by typing or pasting words directly into the app.

Each line is treated as one intended vocabulary item.

### Book Sequence

The ordered vocabulary stream extracted from the user's local IELTS vocabulary book.

It is used as a default source for new daily study words.

The primary book for Version 1 is the user's local copy of `雅思词汇真经PDF高清版.pdf`.

### Book Words CSV

The local import format used to prepare the Book Sequence for Version 1.

It contains the ordered words from the IELTS book and can be produced manually or by an OCR preprocessing step.

### Daily New Word Target

The number of new words the learner wants to study today.

When the learner uses the Book Sequence, this target controls how many next-in-order words are added for the day.

### Sense

One specific meaning of a word, including its part of speech and English definition.

A word can have multiple senses, and each sense can become its own study card.

### Study Example

An example sentence attached to one Sense.

Study examples should be suitable for IELTS learners, either selected from a learner dictionary source or generated for IELTS-style practice.

### Chinese Note

An optional learner-facing note in Chinese.

It supports understanding but is secondary to the English definition and Study Example.
