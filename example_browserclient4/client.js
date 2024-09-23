let socket = new WebSocket("ws://localhost:8001");
let displayDiv = document.getElementById('textDisplay');
let chatDiv = document.getElementById('chatResponses');
let fullSentences = [];

// Function to display the full sentence with a button for Groq interaction
function displayFullSentence(sentence, index) {
    let sentenceDiv = document.createElement('div');
    sentenceDiv.className = index % 2 === 0 ? 'yellow' : 'cyan';
    sentenceDiv.classList.add('text-display');
    
    let sentenceText = document.createElement('span');
    sentenceText.textContent = sentence;

    let sendButton = document.createElement('button');
    sendButton.textContent = "Send to Groq";
    sendButton.onclick = function() {
        sendToGroq(sentence);
    };

    sentenceDiv.appendChild(sentenceText);
    sentenceDiv.appendChild(sendButton);
    displayDiv.appendChild(sentenceDiv);
}

// Display all full sentences
function displayAllFullSentences() {
    displayDiv.innerHTML = "";
    fullSentences.forEach((sentence, index) => {
        displayFullSentence(sentence, index);
    });
}

// Function to send text to Groq via WebSocket
function sendToGroq(text) {
    let message = {
        type: 'groq',
        text: text
    };
    socket.send(JSON.stringify(message));
}

// Append response to the chat sidebar
function appendChatResponse(response) {
    let responseDiv = document.createElement('div');
    responseDiv.textContent = response;
    chatDiv.appendChild(responseDiv);
}

// Send the last 5 sentences to Groq
document.getElementById('sendLastFive').onclick = function() {
    let lastFiveSentences = fullSentences.slice(-5).join(' ');
    sendToGroq(lastFiveSentences);
};

// Handle incoming WebSocket messages
socket.onmessage = function(event) {
    let data = JSON.parse(event.data);

    if (data.type === 'realtime') {
        // Display realtime text (optional, not implemented in this adjustment)
    } else if (data.type === 'fullSentence') {
        fullSentences.push(data.text);
        displayAllFullSentences();
    } else if (data.type === 'groqResponse') {
        appendChatResponse(data.response);
    }
};
