let socket = new WebSocket("ws://localhost:8001");
let displayDiv = document.getElementById('textDisplay');
let chatDiv = document.getElementById('chatResponses');
let fullSentences = [];

// Function to display the full sentence and make the entire div clickable
function displayFullSentence(sentence, index) {
    let sentenceDiv = document.createElement('div');
    sentenceDiv.className = index % 2 === 0 ? 'yellow' : 'cyan';
    sentenceDiv.classList.add('text-display');
    
    sentenceDiv.textContent = sentence;
    
    // Make the entire div clickable
    sentenceDiv.onclick = function() {
        sendToGroq(sentence);
    };

    // Prepend the new sentence to the top of the displayDiv
    displayDiv.insertBefore(sentenceDiv, displayDiv.firstChild);
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

// Append response to the chat sidebar (in reverse order, prepend to top)
function appendChatResponse(response) {
    let responseDiv = document.createElement('div');
    responseDiv.textContent = response;
    
    // Prepend the response to the top of the chatDiv
    chatDiv.insertBefore(responseDiv, chatDiv.firstChild);
}

// Send the last N sentences to Groq
function sendLastNSentences(n) {
    let lastNSentences = fullSentences.slice(0, n).join(' ');
    sendToGroq(lastNSentences);
}

// Add event listener for the screenshot button
document.getElementById('captureScreenshot').onclick = function() {
    let message = {
        type: 'screenshot'
    };
    socket.send(JSON.stringify(message));
};

// Handle incoming WebSocket messages
socket.onmessage = function(event) {
    let data = JSON.parse(event.data);

    if (data.type === 'realtime') {
        // Display realtime text (optional, not implemented in this adjustment)
    } else if (data.type === 'fullSentence') {
        // Add the sentence to the beginning of the array
        fullSentences.unshift(data.text);

        // Prepend the new sentence to the top of the display
        displayFullSentence(data.text, fullSentences.length - 1);
    } else if (data.type === 'groqResponse') {
        appendChatResponse(data.response);
    }
};

// Add event listeners for the buttons in the sidebar
document.querySelectorAll('.sendSentences').forEach(button => {
    button.addEventListener('click', function() {
        let count = parseInt(this.getAttribute('data-count'));
        sendLastNSentences(count);
    });
});
