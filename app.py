from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def index():
	return app.send_static_file("index.html")

@app.route("/message", methods=["POST"])
def message():
	data = request.get_json()
	message = data["message"]
	response = get_chatbot_response(message)
	return jsonify({ "message": response })

def get_chatbot_response(message):
	# Your chatbot logic here
	return "Hello, I am a Python chatbot!"

if __name__ == "__main__":
	app.run()
