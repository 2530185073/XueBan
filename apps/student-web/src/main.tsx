import { createRoot } from 'react-dom/client'
import App from './App'
import '@fontsource-variable/noto-serif-sc/wght.css'
import '@fontsource-variable/noto-sans-sc/wght.css'
import './styles.css'
import './record.css'
import './learning.css'
import './design-system.css'

createRoot(document.getElementById('root')!).render(<App />)
