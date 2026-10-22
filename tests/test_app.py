import pytest

from app import app, tasks


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def reset_tasks():
    """Reset the in-memory task list before each test."""
    tasks.clear()
    tasks.extend([
        {"id": 1, "title": "Project-1 Report"},
        {"id": 2, "title": "Project-2 MMFs"}
    ])


def test_index(client):
    """Render the task tracker and its priority selector."""
    response = client.get('/')

    assert response.status_code == 200
    assert b"Task Tracker" in response.data
    assert b'id="task-priority"' in response.data
    assert b'<option value="Low">Low</option>' in response.data
    assert b'<option value="Medium" selected>Medium</option>' in response.data
    assert b'<option value="High">High</option>' in response.data


def test_get_tasks(client):
    """Return all tasks with missing priorities defaulted to Medium."""
    response = client.get('/api/tasks')

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 2
    assert all(task['priority'] == 'Medium' for task in data)


def test_add_task_success(client):
    """Create a task and return its title and default priority."""
    response = client.post('/api/tasks', json={"title": "Test new task"})

    assert response.status_code == 201
    assert response.get_json()['title'] == "Test new task"
    assert response.get_json()['priority'] == "Medium"

    get_response = client.get('/api/tasks')
    assert len(get_response.get_json()) == 3


@pytest.mark.parametrize('priority', ['Low', 'Medium', 'High'])
def test_add_task_with_priority(client, priority):
    """Create a task with each supported priority value."""
    response = client.post(
        '/api/tasks',
        json={"title": f"{priority} task", "priority": priority}
    )

    assert response.status_code == 201
    task = response.get_json()
    assert task['title'] == f"{priority} task"
    assert task['priority'] == priority

    get_response = client.get('/api/tasks')
    created_task = get_response.get_json()[-1]
    assert created_task['priority'] == priority


def test_add_task_missing_priority_defaults_to_medium(client):
    """Default an omitted priority to Medium when creating a task."""
    response = client.post('/api/tasks', json={"title": "No priority"})

    assert response.status_code == 201
    assert response.get_json()['priority'] == 'Medium'


def test_add_task_invalid_priority(client):
    """Reject priorities that are not Low, Medium, or High."""
    response = client.post(
        '/api/tasks',
        json={"title": "Invalid priority", "priority": "Urgent"}
    )

    assert response.status_code == 400
    assert response.get_json()['error'] == (
        'Priority must be Low, Medium, or High'
    )
    assert len(tasks) == 2


def test_frontend_renders_returned_priority(client):
    """Verify frontend code renders priority values and fallback behavior."""
    response = client.get('/static/app.js')
    javascript = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "task.priority || 'Medium'" in javascript
    assert 'priorityBadge.textContent = priority' in javascript
    assert 'priority-badge' in javascript


def test_add_task_missing_title(client):
    """Reject task creation requests without a title."""
    response = client.post('/api/tasks', json={"wrong_key": "value"})

    assert response.status_code == 400
    assert "error" in response.get_json()

    response2 = client.post('/api/tasks', json={})
    assert response2.status_code == 400


def test_delete_task(client):
    """Delete the task with the specified ID."""
    response = client.delete('/api/tasks/1')

    assert response.status_code == 200
    get_response = client.get('/api/tasks')
    data = get_response.get_json()
    assert len(data) == 1
    assert data[0]['id'] == 2
