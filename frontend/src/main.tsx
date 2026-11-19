import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { VocabApp } from './VocabApp';
import './styles.css';
import './auth.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <VocabApp />
  </StrictMode>
);
