import React, { useEffect } from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard.jsx';
import AgentResult from './pages/AgentResult.jsx';
import Login from './pages/Login.jsx';

const bodyClassMap = {
  '/login': 'login-view',
  '/login.html': 'login-view',
  '/agent-result': 'standalone-view',
  '/agent_result.html': 'standalone-view'
};

export default function App() {
  const location = useLocation();

  useEffect(() => {
    const classNames = Object.values(bodyClassMap);
    document.body.classList.remove(...classNames);
    const match = bodyClassMap[location.pathname];
    if (match) {
      document.body.classList.add(match);
    }
  }, [location.pathname]);

  return (
    <Routes>
      <Route path="/login.html" element={<Login />} />
      <Route path="/login" element={<Login />} />
      <Route path="/agent_result.html" element={<AgentResult />} />
      <Route path="/agent-result" element={<AgentResult />} />
      <Route path="/index.html" element={<Dashboard />} />
      <Route path="/" element={<Dashboard />} />
      <Route path="*" element={<Dashboard />} />
    </Routes>
  );
}
