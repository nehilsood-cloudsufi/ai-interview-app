/**
 * React entry point: mounts <App> into the #root element under StrictMode
 * and pulls in the global stylesheet. No app logic lives here.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
