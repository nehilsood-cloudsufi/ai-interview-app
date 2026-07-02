import React from 'react';
import { useInterview } from '../context/InterviewContext';

export const Completion: React.FC = () => {
  const { setToken, setStage } = useInterview();

  const handleRestart = () => {
    setToken(null);
    setStage('greeting');
  };

  return (
    <div style={{ padding: '2rem', textAlign: 'center' }}>
      <h1>Interview Complete</h1>
      <p>Thank you for completing the interview. We will review your transcript and get back to you soon.</p>
      
      <button 
        onClick={handleRestart}
        style={{ marginTop: '2rem', padding: '0.5rem 1rem', cursor: 'pointer' }}
      >
        Return to Home
      </button>
    </div>
  );
};
