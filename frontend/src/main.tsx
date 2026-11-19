import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { VocabApp } from './VocabApp';
import { IcpFooter } from './components/IcpFooter';
import './styles.css';
import './auth.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <VocabApp />
    <IcpFooter />
  </StrictMode>
);
