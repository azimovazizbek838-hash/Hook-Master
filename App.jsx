import React from 'react';

export default function App() {
  const connectTrustWallet = async () => {
    if (typeof window.ethereum !== 'undefined') {
      try {
        const accounts = await window.ethereum.request({ 
          method: 'eth_requestAccounts' 
        });
        const userWalletAddress = accounts[0];
        alert("Ulangan manzil: " + userWalletAddress);
        return userWalletAddress;
      } catch (error) {
        console.error("Ulanishda xatolik:", error);
      }
    } else {
      alert("Trust Wallet topilmadi! Iltimos, hamyonni o'rnating.");
    }
  };

  return (
    <div style={{ textAlign: 'center', marginTop: '100px', fontFamily: 'sans-serif' }}>
      <h1>Hook Master AI</h1>
      <p>Trust Wallet'ni ulash uchun pastdagi tugmani bosing:</p>
      <button 
        onClick={connectTrustWallet} 
        style={{ 
          padding: '12px 24px', 
          fontSize: '16px', 
          backgroundColor: '#3375BB', 
          color: '#fff', 
          border: 'none', 
          borderRadius: '8px', 
          cursor: 'pointer' 
        }}
      >
        Trust Wallet'ni Ulash
      </button>
    </div>
  );
}
