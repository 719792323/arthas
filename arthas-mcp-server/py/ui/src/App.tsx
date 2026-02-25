/**
 * 应用路由配置
 */

import { BrowserRouter, Routes, Route } from 'react-router-dom';
import TaskList from './pages/TaskList';
import TaskDetail from './pages/TaskDetail';
import PendingApproval from './pages/PendingApproval';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskList />} />
        <Route path="/task/:taskId" element={<TaskDetail />} />
        <Route path="/pending-approval" element={<PendingApproval />} />
      </Routes>
    </BrowserRouter>
  );
}
