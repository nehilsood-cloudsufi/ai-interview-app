import React, { useState } from 'react';
import { useInterview } from '../context/InterviewContext';

export const Landing: React.FC = () => {
  const { setParticipantName, setJobRole, setRoomName, setToken } = useInterview();
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError('Please enter your name');
      return;
    }
    
    setLoading(true);
    setError('');
    
    try {
      const generatedRoom = `interview-${Date.now()}`;
      
      const response = await fetch(`${import.meta.env.VITE_API_URL}/token?room=${generatedRoom}&name=${encodeURIComponent(name)}`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch token');
      }
      
      const data = await response.json();
      
      setParticipantName(name);
      setJobRole(role);
      setRoomName(generatedRoom);
      setToken(data.token);
    } catch (err) {
      console.error('Error starting interview:', err);
      setError('Failed to start interview. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="landing-container" style={{ padding: '2rem', maxWidth: '400px', margin: '0 auto', textAlign: 'center' }}>
      <h1>AI Interview App</h1>
      <p>Welcome to your automated interview experience.</p>
      
      <form onSubmit={handleStart} style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '2rem' }}>
        <div>
          <input 
            type="text" 
            placeholder="Your Name" 
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={loading}
            style={{ padding: '0.5rem', width: '100%', boxSizing: 'border-box' }}
          />
        </div>
        <div>
          <input 
            type="text" 
            placeholder="Target Role (e.g., Frontend Engineer)" 
            value={role}
            onChange={(e) => setRole(e.target.value)}
            disabled={loading}
            style={{ padding: '0.5rem', width: '100%', boxSizing: 'border-box' }}
          />
        </div>
        
        {error && <div style={{ color: 'red' }}>{error}</div>}
        
        <button 
          type="submit" 
          disabled={loading}
          style={{ padding: '0.5rem 1rem', cursor: loading ? 'not-allowed' : 'pointer' }}
        >
          {loading ? 'Connecting...' : 'Start Interview'}
        </button>
      </form>
    </div>
  );
};
