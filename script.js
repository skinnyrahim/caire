const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatOutput = document.getElementById("chat-output");

chatForm.addEventListener("submit", (event) => {
	event.preventDefault();
	const message = chatInput.value;
	sendMessage(message);
});

function sendMessage(message) {
	const xhr = new XMLHttpRequest();
	xhr.open("POST", "/message");
	xhr.setRequestHeader("Content-Type", "application/json");
	xhr.onload = () => {
		const response = JSON.parse(xhr.responseText);
		updateChat(response.message);
	};
	const data = JSON.stringify({ message: message });
	xhr.send(data);
}

function updateChat(message) {
	const chatBubble = document.createElement("div");
	chatBubble.classList.add("chat-bubble");
	chatBubble.innerText = message;
	chatOutput.appendChild(chatBubble);
}
