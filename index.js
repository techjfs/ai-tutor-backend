// 建立 WebSocket 连接
const ws = new WebSocket('ws://localhost:8050/ws/llm');
let currentTaskId = null;

// 发送问题
function sendQuestion(question) {
  ws.send(JSON.stringify({
    type: 'question',
    question: question
  }));

  // 清空之前的回复区域
  document.getElementById('response').innerHTML = '';

  // 显示停止按钮
  document.getElementById('stopButton').style.display = 'block';
}

// 停止生成
function stopGeneration() {
  if (currentTaskId) {
    ws.send(JSON.stringify({
      type: 'stop',
      task_id: currentTaskId
    }));
  }
}

// 处理服务器消息
ws.onmessage = function(event) {
  const message = JSON.parse(event.data);

  switch(message.type) {
    case 'task_started':
      currentTaskId = message.task_id;
      console.log('Task started with ID:', currentTaskId);
      break;

    case 'llm_response':
      const responseArea = document.getElementById('response');

      switch(message.event) {
        case 'start':
          responseArea.innerHTML = '';
          break;

        case 'message':
          // 追加文本
          responseArea.innerHTML += message.data;
          break;

        case 'interrupted':
          responseArea.innerHTML += '\n[Generation interrupted]';
          document.getElementById('stopButton').style.display = 'none';
          currentTaskId = null;
          break;

        case 'error':
          responseArea.innerHTML += `\n[Error: ${message.data}]`;
          document.getElementById('stopButton').style.display = 'none';
          currentTaskId = null;
          break;

        case 'end':
          document.getElementById('stopButton').style.display = 'none';
          currentTaskId = null;
          break;
      }
      break;

    case 'command_sent':
      console.log(`Command ${message.command} sent for task ${message.task_id}`);
      break;
  }
};

// 连接关闭处理
ws.onclose = function() {
  console.log('WebSocket connection closed');
};