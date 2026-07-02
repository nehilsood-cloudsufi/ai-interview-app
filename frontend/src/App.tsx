import React from 'react';
import { InterviewProvider, useInterview } from './context/InterviewContext';
import { Landing } from './components/Landing';
import { InterviewRoom } from './components/LiveKitRoom';
import { Completion } from './components/Completion';
import './index.css';

const AppContent: React.FC = () => {
  const { token, stage } = useInterview();

  if (stage === 'completed') {
    return <Completion />;
  }

  if (token) {
    return <InterviewRoom />;
  }

  return <Landing />;
};

function App() {
  return (
    <InterviewProvider>
      <AppContent />
    </InterviewProvider>
  );
}

export default App;
