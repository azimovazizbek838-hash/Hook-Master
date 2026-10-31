const generateHook = async (promptText) => {
  // 1-urinish: Google Gemini API
  try {
    const response = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key=${import.meta.env.VITE_GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: promptText }] }] })
      }
    );
    if (!response.ok) throw new Error("Gemini limitga uchradi");
    const data = await response.json();
    return data.candidates[0].content.parts[0].text;
  } catch (err) {
    console.warn("Gemini ishlamadi, Groq'ga yo'naltirilmoqda...", err);
  }

  // 2-urinish (Failover): Groq API (Llama3)
  try {
    const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${import.meta.env.VITE_GROQ_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile",
        messages: [{ role: "user", content: promptText }]
      })
    });
    if (!response.ok) throw new Error("Groq limitga uchradi");
    const data = await response.json();
    return data.choices[0].message.content;
  } catch (err) {
    console.warn("Groq ishlamadi, OpenRouter'ga yo'naltirilmoqda...", err);
  }

  // 3-urinish (Failover): OpenRouter (Bepul modellar)
  try {
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${import.meta.env.VITE_OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "auto", // yoki "google/gemini-2.0-flash-lite-preview-02-05:free"
        messages: [{ role: "user", content: promptText }]
      })
    });
    const data = await response.json();
    return data.choices[0].message.content;
  } catch (err) {
    console.error("Barcha bepul AI manbalari limitga yetdi!", err);
    throw new Error("Hozircha barcha bepul serverlar band. Birozdan so'ng urinib ko'ring.");
  }
};
