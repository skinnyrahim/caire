$(document).ready(function() {
    $('#chat-form').submit(function(e) {
        e.preventDefault();
        var message = $('#chat-input').val();
        $('#chat-input').val('');
        $.ajax({
            url: 'chatbot.php',
            type: 'POST',
            data: { message: message },
            success: function(response) {
                $('#chat-output').append('<p><strong>You:</strong> ' + message + '</p><p><strong>Chatbot:</strong> ' + response + '</p>');
            }
        });
    });
});
