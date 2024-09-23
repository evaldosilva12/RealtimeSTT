let socket = new WebSocket("ws://localhost:8001");
let displayDiv = document.getElementById('textDisplay');
let chatDiv = document.getElementById('chatResponses');
let fullSentences = [];

// Function to display the transcription on the screen
function displayTranscription(sentence) {
    let sentenceDiv = document.createElement('div');
    sentenceDiv.className = fullSentences.length % 2 === 0 ? 'yellow' : 'cyan';
    sentenceDiv.classList.add('text-display');
    
    let sentenceText = document.createElement('span');
    sentenceText.textContent = sentence;

    displayDiv.appendChild(sentenceDiv);
    sentenceDiv.appendChild(sentenceText);
}

// Function to append feedback to the chat
function appendChatResponse(response, useful) {
    let responseDiv = document.createElement('div');
    responseDiv.textContent = response;
    responseDiv.style.color = useful ? 'yellow' : 'gray';  // Useful responses in yellow, incomplete in gray
    chatDiv.appendChild(responseDiv);
}

// Function to send text to Groq via WebSocket
function sendToGroq(text) {
    console.log("Sending to Groq:", text);  // Log the text being sent to Groq
    let message = {
        type: 'groq',
        text: text
    };
    socket.send(JSON.stringify(message));
}

// Handle incoming WebSocket messages
socket.onmessage = function(event) {
    let data = JSON.parse(event.data);

    if (data.type === 'realtime') {
        // Optionally handle real-time text (not implemented here)
    } else if (data.type === 'fullSentence') {
        // Push full sentence into the array
        fullSentences.push(data.text);

        // Display the transcription on the screen
        displayTranscription(data.text);

        // Automatically send the completed sentence to Groq
        sendToGroq(data.text);
    } else if (data.type === 'groqResponse') {
        // Append Groq feedback to the chat
        appendChatResponse(data.response, data.useful);
    }
};

// Send the last 5 sentences to Groq when the button is clicked
document.getElementById('sendLastFive').onclick = function() {
    let lastFiveSentences = fullSentences.slice(-5).join(' ');
    sendToGroq(lastFiveSentences);
};
