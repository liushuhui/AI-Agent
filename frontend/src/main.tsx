import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* history 路由：开发/预览由 Vite 兜底返回 index.html；静态部署时需配置 SPA 回退 */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
