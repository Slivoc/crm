import os
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, jsonify, current_app, render_template, send_from_directory, flash, session
from werkzeug.utils import secure_filename
from db import db_cursor, execute as db_execute
from models import get_rfqs_for_project, insert_stage_update, get_stage_updates, insert_file_for_project_stage, insert_project, get_stage, get_stage_by_id, insert_project_stage, get_project_stages, generate_breadcrumbs, update_project, get_project_by_id, get_projects, insert_project_update, \
    get_project_updates, get_project_statuses, get_customers, get_salespeople, insert_file_for_project, link_file_to_project, get_files_for_project, get_file_by_id
from routes.auth import login_required, current_user

projects_bp = Blueprint('projects', __name__)


@projects_bp.before_request
def require_login():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.url))


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None, fetch=None):
    cur.execute(_prepare_query(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return cur

@projects_bp.route('/new', methods=['POST'])
def create_project():
    try:
        customer_id = request.form['customer_id']
        salesperson_id = request.form['salesperson_id']
        name = request.form['name']
        description = request.form.get('description', '')  # Default to empty string if not provided
        status_id = request.form.get('status_id', 1)

        project_id = insert_project(customer_id, salesperson_id, name, description, status_id)
        return jsonify({'success': True, 'project_id': project_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@projects_bp.route('/<int:project_id>/edit', methods=['GET', 'POST'])
def edit_project(project_id):
    project = get_project_by_id(project_id)  # Fetch project, including description
    statuses = get_project_statuses()
    updates = get_project_updates(project_id)
    salespeople = get_salespeople()
    customers = get_customers()

    if request.method == 'POST':
        try:
            name = request.form['name']
            description = request.form.get('description', '')
            customer_id = request.form['customer_id']
            salesperson_id = request.form.get('salesperson_id')
            status_id = request.form['status_id']

            # Update to match parameter order in models.py
            update_project(project_id, customer_id, salesperson_id, name, description, status_id)
            return redirect(url_for('projects.edit_project', project_id=project_id))
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    stages = get_project_stages(project_id)
    recurrence_types = db_execute('SELECT * FROM recurrence_types', fetch='all') or []
    rendered_stages = ""
    for stage in stages:
        rendered_stages += render_stage(stage, recurrence_types, updates)

    breadcrumbs = generate_breadcrumbs(
        ('Edit Project #{}'.format(project_id), url_for('projects.edit_project', project_id=project_id))
    )

    project_rfqs = get_rfqs_for_project(project_id)

    return render_template(
        'project_edit.html',
        project=project,
        statuses=statuses,
        updates=updates,
        files=get_files_for_project(project_id),
        breadcrumbs=breadcrumbs,
        stages=stages,
        salespeople=salespeople,
        customers=customers,
        recurrence_types=recurrence_types,
        rendered_stages=rendered_stages,
        get_project_stages=get_project_stages,
        project_rfqs=project_rfqs  # Add this line to pass RFQs to template
    )


@projects_bp.route('/<int:project_id>/update', methods=['POST'])
def update_project_route(project_id):
    print("Received form data:", dict(request.form))
    try:
        # Get form data
        name = request.form.get('name')
        description = request.form.get('description', '').strip()
        customer_id = request.form.get('customer_id')
        salesperson_id = request.form.get('salesperson_id') or None
        status_id = request.form.get('status_id')

        # Validate required fields
        if not all([name, customer_id, status_id]):
            flash('Missing required fields', 'error')
            return redirect(url_for('projects.edit_project', project_id=project_id))

        # Validate IDs are integers
        try:
            customer_id = int(customer_id)
            status_id = int(status_id)
            if salesperson_id:
                salesperson_id = int(salesperson_id)
        except ValueError:
            flash('Invalid ID format', 'error')
            return redirect(url_for('projects.edit_project', project_id=project_id))

        # Update project with parameters in correct order
        update_project(project_id, customer_id, salesperson_id, name, description, status_id)
        flash('Project updated successfully', 'success')
        return redirect(url_for('projects.edit_project', project_id=project_id))

    except Exception as e:
        flash(f'Error updating project: {str(e)}', 'error')
        return redirect(url_for('projects.edit_project', project_id=project_id))


@projects_bp.route('/<int:project_id>/add_update', methods=['POST'])
@login_required
def add_project_update(project_id):
    comment = request.form.get('comment', '').strip()
    if not comment:
        return jsonify({"success": False, "error": "Comment is required."}), 400

    # Use the method to get the salesperson ID
    salesperson_id = current_user.get_salesperson_id()
    if not salesperson_id:
        return jsonify({"success": False, "error": "No salesperson associated with this user."}), 400

    # Insert the update into the database
    insert_project_update(project_id, salesperson_id, comment)

    return jsonify({"success": True})



@projects_bp.route('/', methods=['GET', 'POST'])
def list_projects():
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        salesperson_id = request.form['salesperson_id']
        name = request.form['name']
        description = request.form.get('description')
        status_id = request.form.get('status_id', 1)

        project_id = insert_project(customer_id, salesperson_id, name, description, status_id)
        return redirect(url_for('projects.list_projects'))

    show_all = request.args.get('show_all', '0') == '1'

    # Filter projects based on show_all parameter
    if show_all:
        projects = get_projects()
    else:
        projects = get_projects(salesperson_id=current_user.get_salesperson_id())

    active_project = None
    if 'active_project_id' in session:
        active_project = get_project_by_id(session['active_project_id'])

    for project in projects:
        if project['next_stage_deadline']:
            try:
                from datetime import datetime
                deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                project['next_stage_deadline_formatted'] = project['next_stage_deadline']
        else:
            project['next_stage_deadline_formatted'] = None

        project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
            'estimated_value'] else None

    customers = get_customers()
    salespeople = get_salespeople()
    statuses = get_project_statuses()

    return render_template('projects.html',
                           projects=projects,
                           customers=customers,
                           salespeople=salespeople,
                           statuses=statuses,
                           project=active_project,
                           show_all=show_all,
                           get_project_stages=get_project_stages)


ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'jpg', 'png', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@projects_bp.route('/<int:project_id>/upload', methods=['POST'])
def upload_file(project_id):
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part in request'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        try:
            filename = secure_filename(file.filename)
            upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'projects')
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)

            filepath = os.path.join(upload_folder, filename)
            file.save(filepath)

            file_id = insert_file_for_project(filename, filepath, datetime.now().date())
            link_file_to_project(project_id, file_id)

            # Files are already dictionaries from dict_from_row
            files = get_files_for_project(project_id)
            return jsonify({
                'success': True,
                'files': files
            })

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    else:
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400

@projects_bp.route('/download/<int:file_id>', methods=['GET'])
def download_file(file_id):
    # Get the file from the database using the file_id
    file = get_file_by_id(file_id)
    if file:
        return send_from_directory(directory=os.path.dirname(file['filepath']),
                                   filename=os.path.basename(file['filepath']), as_attachment=True)
    else:
        return jsonify({'error': 'File not found'}), 404


@projects_bp.route('/<int:project_id>/add_stage', methods=['POST'])
def add_stage(project_id):
    data = request.get_json()  # Parse JSON data for AJAX request

    # Add debugging
    print(f"Received data: {data}")

    name = data.get('name', 'New Stage')
    status_id = data.get('status_id', 1)
    parent_stage_id = data.get('parent_stage_id')

    # More debugging
    print(f"name: {name}")
    print(f"status_id: {status_id}")
    print(f"parent_stage_id: {parent_stage_id} (type: {type(parent_stage_id)})")

    due_date = None

    new_stage_id = insert_project_stage(project_id, name, None, parent_stage_id, status_id, due_date)
    print(f"Created stage with ID: {new_stage_id}")

    return jsonify({'success': True, 'new_stage_id': new_stage_id})

@projects_bp.route('/<int:project_id>/edit_stage/<int:stage_id>', methods=['GET', 'POST'])
def edit_stage(stage_id, project_id):
    if request.method == 'GET':
        stage = get_stage(stage_id)
        return jsonify({
            'name': stage['name'],
            'description': stage['description'],
            'files': stage['files'],
            'updates': stage['updates']
        })

    # Handle POST
    data = request.json

    # Get current stage data
    current_stage = get_stage(stage_id)

    # Update only the fields that were sent
    name = data.get('name', current_stage['name'])
    description = data.get('description', current_stage['description'])
    status_id = data.get('status_id')

    update_project_stage(stage_id, name, description, status_id=status_id)
    return jsonify({'success': True})


def update_project_stage(stage_id, name, description, status_id=None):
    try:
        update_fields = []
        params = []

        if name is not None:
            update_fields.append("name = ?")
            params.append(name)
        if description is not None:
            update_fields.append("description = ?")
            params.append(description)
        if status_id is not None:
            update_fields.append("status_id = ?")
            params.append(status_id)

        if not update_fields:
            return True

        params.append(stage_id)

        query = f"""
            UPDATE project_stages 
            SET {', '.join(update_fields)}
            WHERE id = ?
        """

        db_execute(query, params, commit=True)
        return True
    except Exception as e:
        print(f"Error updating stage: {e}")
        return False

@projects_bp.route('/update_stage_recurrence/<int:stage_id>', methods=['POST'])
def update_stage_recurrence(stage_id):
    data = request.get_json()
    recurrence_id = data.get('recurrence_id', None)

    # Update the recurrence_id in the database
    db_execute('UPDATE project_stages SET recurrence_id = ? WHERE id = ?', (recurrence_id, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:stage_id>/update_stage_description', methods=['POST'])
def update_stage_description(stage_id):
    data = request.get_json()
    description = data.get('description')

    # Update the stage description in the project_stages table
    db_execute('UPDATE project_stages SET description = ? WHERE id = ?', (description, stage_id), commit=True)

    return jsonify({"success": True})




def generate_stage_breadcrumbs(stage, project_id):
    breadcrumbs = []
    current_stage = stage

    # Traverse up through parent stages to build the breadcrumb trail
    while current_stage:
        # Insert the current stage's name and URL at the beginning of the breadcrumbs list
        breadcrumbs.insert(0, (
        current_stage['name'], url_for('projects.edit_stage', project_id=project_id, stage_id=current_stage['id'])))

        # Fetch the parent stage using parent_stage_id
        parent_stage_id = current_stage.get('parent_stage_id')
        if parent_stage_id:
            current_stage = get_stage_by_id(parent_stage_id)  # Fetch the parent stage by its ID
        else:
            current_stage = None  # No parent stage

    print(f"Final breadcrumbs: {breadcrumbs}")  # Debug final breadcrumbs
    return breadcrumbs

@projects_bp.route('/<int:stage_id>/update_stage_name', methods=['POST'])
def update_stage_name(stage_id):
    data = request.get_json()
    new_name = data.get('name')

    # Ensure the new name is valid
    if not new_name or new_name.strip() == "":
        return jsonify({"success": False, "error": "Stage name cannot be empty."}), 400

    # Update the stage name in the project_stages table
    db_execute('UPDATE project_stages SET name = ? WHERE id = ?', (new_name, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:substage_id>/update_substage_name', methods=['POST'])
def update_substage_name(substage_id):
    data = request.get_json()
    new_name = data.get('name')

    # Ensure the new name is valid
    if not new_name or new_name.strip() == "":
        return jsonify({"success": False, "error": "Substage name cannot be empty."}), 400

    # Update the substage name in the project_stages table (assuming substages are also stored in the same table)
    db_execute('UPDATE project_stages SET name = ? WHERE id = ?', (new_name, substage_id), commit=True)

    return jsonify({"success": True})


@projects_bp.route('/<int:parent_stage_id>/add_substage', methods=['POST'])
def add_substage(parent_stage_id):
    try:
        # Parse the JSON data from the request
        data = request.get_json()
        name = data.get('name')
        status_id = data.get('status_id', 1)  # Default to 1 (incomplete) if not provided
        project_id = data.get('project_id')  # Expect the project_id in the request

        if not name or not project_id:
            return jsonify({"success": False, "error": "Name and project_id are required"}), 400

        # Insert the new sub-stage into the database
        row = db_execute(
            '''
            INSERT INTO project_stages (name, parent_stage_id, status_id, project_id)
            VALUES (?, ?, ?, ?)
            RETURNING id
            ''',
            (name, parent_stage_id, status_id, project_id),
            fetch='one',
            commit=True,
        )

        new_substage_id = row.get('id', list(row.values())[0]) if row else None
        if new_substage_id is None:
            raise RuntimeError("Failed to insert sub-stage")

        return jsonify({"success": True, "new_substage_id": new_substage_id})

    except Exception as e:
        print(f"Error while adding sub-stage: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def render_stage(stage, recurrence_types, updates):
    # Determine if the checkbox should be checked based on the status
    checked = 'checked' if stage['status_id'] == 2 else ''

    # Handle recurrence dropdown options
    if not recurrence_types:
        recurrence_options = '<option value="" disabled>No recurrence types available</option>'
    else:
        recurrence_id = stage.get('recurrence_id', None)
        recurrence_options = ''.join([
            f'<option value="{recurrence["id"]}" {"selected" if recurrence["id"] == recurrence_id else ""}>{recurrence["name"]}</option>'
            for recurrence in recurrence_types
        ])

    # Create the updates section
    update_items = ""
    for update in updates:
        if update['stage_id'] == stage['id']:
            update_items += f"""
            <li class="list-group-item">
                {update['comment']} 
                <br> <small class="text-muted">Posted on: {update['date_created']}</small>
            </li>
            """

    if not update_items:
        update_items = '<li class="list-group-item text-muted">No updates available.</li>'

    # Render the stage accordion item using Bootstrap grid
    rendered = f"""
    <div class="accordion-item" id="stage-{stage['id']}">
        <h2 class="accordion-header" id="heading-stage-{stage['id']}">
            <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-stage-{stage['id']}" aria-expanded="false" aria-controls="collapse-stage-{stage['id']}">
                <input type="checkbox" class="form-check-input me-2" id="stageCheck{stage['id']}" {checked} onchange="toggleStageStatus('{stage['id']}')">
                <span class="editable-text" contenteditable="true" id="stageName{stage['id']}" oninput="updateStageName('{stage['id']}')">
                    {stage['name']}
                </span>
                <i class="bi bi-trash ms-2" style="cursor: pointer;" onclick="deleteStage('{stage['id']}')"></i>
            </button>
        </h2>
        <div id="collapse-stage-{stage['id']}" class="accordion-collapse collapse" aria-labelledby="heading-stage-{stage['id']}">
            <div class="accordion-body">
                <div class="row">
                    <!-- Left column: Stage details -->
                    <div class="col-md-6">
                        <div class="mb-3">
                            <label for="description-{stage['id']}" class="form-label">Description:</label>
                            <textarea class="form-control" id="description-{stage['id']}" rows="3" oninput="updateStageDescription('{stage['id']}')">{stage['description']}</textarea>
                        </div>
                        <div class="mb-3">
                            <label for="recurrence-{stage['id']}" class="form-label">Recurrence:</label>
                            <select class="form-select" id="recurrence-{stage['id']}" onchange="updateStageRecurrence('{stage['id']}')">
                                {recurrence_options}
                            </select>
                        </div>
                    </div>

                    <!-- Right column: Updates section -->
                    <div class="col-md-6">
                        <h6>Updates</h6>
                        <ul class="list-group">
                            {update_items}
                        </ul>
                        <form action="/projects/{stage['id']}/add_update" method="post" class="mt-3">
                            <textarea class="form-control mb-2" name="comment" placeholder="Add new update" rows="2" required></textarea>
                            <button type="submit" class="btn btn-primary btn-sm">Add Update</button>
                        </form>
                    </div>
                </div>
                <div class="accordion mt-3" id="substageAccordion-{stage['id']}">
    """

    # Recursively render substages, if any
    for substage in stage.get('substages', []):
        rendered += render_stage(substage, recurrence_types, updates)

    # Close the accordion body and add the "Add Sub-Stage" button
    rendered += f"""
                </div>
                <button class="btn btn-sm btn-primary mt-3" onclick="showNewSubStageForm('{stage['id']}')">+</button>
            </div>
        </div>
    </div>
    """

    return rendered


@projects_bp.route('/<int:stage_id>/update_stage_status', methods=['POST'])
def update_stage_status(stage_id):
    data = request.get_json()
    new_status = data.get('status')

    # Update the validation to accept 1 (incomplete), 2 (complete), and 3 (deleted)
    if new_status not in [1, 2, 3]:
        return jsonify({"success": False, "error": "Invalid status value."}), 400

    # Update the stage status in the project_stages table
    db_execute('UPDATE project_stages SET status_id = ? WHERE id = ?', (new_status, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:project_id>/add_update', methods=['POST'])
def add_update(project_id):
    try:
        comment = request.form['comment']
        salesperson_id = request.form['salesperson_id']  # Replace with logged-in user ID
        insert_project_update(project_id, salesperson_id, comment)

        # Fetch updated list of updates
        updates = get_project_updates(project_id)
        return jsonify({'success': True, 'updates': updates})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/quick_update', methods=['POST'])
def quick_update_project(project_id):
    try:
        next_stage_name = request.form.get('next_stage_name')
        next_stage_deadline = request.form.get('next_stage_deadline')
        estimated_value = request.form.get('estimated_value')
        estimated_value_input = estimated_value

        with db_cursor(commit=True) as cursor:
            current_values = _execute_with_cursor(
                cursor,
                'SELECT next_stage_id, next_stage_deadline, estimated_value FROM projects WHERE id = ?',
                (project_id,),
                fetch='one'
            )
            if not current_values:
                raise RuntimeError(f"Project not found: {project_id}")

            next_stage_id = current_values['next_stage_id']

            if next_stage_name:
                stage_row = _execute_with_cursor(
                    cursor,
                    'SELECT id FROM project_stages WHERE project_id = ? AND name = ?',
                    (project_id, next_stage_name),
                    fetch='one'
                )
                if stage_row:
                    next_stage_id = stage_row['id']
                else:
                    insert_row = _execute_with_cursor(
                        cursor,
                        'INSERT INTO project_stages (project_id, name, status_id) VALUES (?, ?, ?) RETURNING id',
                        (project_id, next_stage_name, 1),
                        fetch='one'
                    )
                    if insert_row:
                        next_stage_id = insert_row.get('id', list(insert_row.values())[0])

            next_stage_deadline = next_stage_deadline or current_values['next_stage_deadline']
            estimated_value = float(estimated_value_input) if estimated_value_input else current_values['estimated_value']

            _execute_with_cursor(
                cursor,
                '''
                UPDATE projects 
                SET next_stage_id = ?, 
                    next_stage_deadline = ?, 
                    estimated_value = ?
                WHERE id = ?
                ''',
                (next_stage_id, next_stage_deadline, estimated_value, project_id)
            )

            stage_name = None
            if next_stage_id:
                stage_row = _execute_with_cursor(
                    cursor,
                    'SELECT name FROM project_stages WHERE id = ?',
                    (next_stage_id,),
                    fetch='one'
                )
                stage_name = stage_row['name'] if stage_row else None

        return jsonify({
            'success': True,
            'next_stage_id': next_stage_id,
            'next_stage_name': stage_name,
            'next_stage_deadline': next_stage_deadline,
            'estimated_value': estimated_value
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@projects_bp.route('/<int:project_id>/upload_stage_file', methods=['POST'])
def upload_stage_file(project_id):
    stage_id = request.form.get('stage_id')
    file = request.files.get('file')

    if not stage_id or not file:
        return jsonify({'success': False, 'error': 'Missing stage_id or file'})

    # Save the file to the server
    upload_dir = 'uploads/'
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)

    filename = file.filename
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)

    # Insert file and link it to the stage
    upload_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    file_id = insert_file_for_project_stage(stage_id, filename, filepath, upload_date)

    return jsonify({'success': True, 'file_id': file_id})

@projects_bp.route('/<int:project_id>/files', methods=['GET'])
def get_project_files_route(project_id):
    try:
        files = get_files_for_project(project_id)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Route for adding stage updates
@projects_bp.route('/<int:project_id>/stages/<int:stage_id>/add_update', methods=['POST'])
def add_stage_update(project_id, stage_id):
    salesperson_id = request.form.get('salesperson_id')
    comment = request.form.get('comment', '').strip()

    if not salesperson_id or not comment:
        return jsonify({"success": False, "error": "Salesperson and comment are required."}), 400

    try:
        # Insert the update
        insert_stage_update(stage_id, salesperson_id, comment)

        # Get updated list of stage updates
        updates = get_stage_updates(stage_id)

        return jsonify({
            "success": True,
            "updates": updates
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@projects_bp.route('/sidebar-stages/<int:project_id>')
def get_sidebar_stages(project_id):
    project = get_project_by_id(project_id)

    # Debug: Check raw database data BEFORE processing
    raw_stages = db_execute(
        '''
        SELECT id, name, description, status_id
        FROM project_stages
        WHERE project_id = ? AND status_id != 3
        ORDER BY id
        ''',
        (project_id,),
        fetch='all'
    ) or []

    print(f"\n=== RAW DATABASE DATA for project {project_id} ===")
    for stage in raw_stages:
        stage_dict = dict(stage)
        desc = stage_dict['description']
        print(f"Stage {stage_dict['id']}:")
        print(f"  - description value: {repr(desc)}")
        print(f"  - description type: {type(desc)}")
        print(f"  - is None?: {desc is None}")
        print(f"  - equals 'None'?: {desc == 'None'}")
        print(f"  - length: {len(desc) if desc else 'N/A'}")

    # Now get processed stages
    stages = get_project_stages(project_id)

    print(f"\n=== PROCESSED DATA ===")
    for stage in stages:
        desc = stage.get('description')
        print(f"Stage {stage.get('id')}:")
        print(f"  - description value: {repr(desc)}")
        print(f"  - description type: {type(desc)}")
        print(f"  - is None?: {desc is None}")
        print(f"  - equals 'None'?: {desc == 'None'}")
        print(f"  - truthiness: {bool(desc)}")
        print(f"  - length check would pass?: {bool(desc and len(str(desc)) > 0)}")

    # Make sure get_project_stages is available in template context
    return render_template('components/project_stages_list.html',
                           project=project,
                           stages=stages,
                           get_project_stages=get_project_stages)  # Add this line!

@projects_bp.route('/set-active/<int:project_id>')
def set_active_project(project_id):
    session['active_project_id'] = project_id
    project = get_project_by_id(project_id)
    return jsonify(
        success=True,
        projectName=project['name'] if project else "Selected Project"
    )

@projects_bp.route('/<int:project_id>/stages', methods=['GET'])
def get_project_stages_api(project_id):
    try:
        stages = get_project_stages(project_id)  # Fetch stages from the database
        return jsonify({'success': True, 'stages': stages})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@projects_bp.route('/kanban', methods=['GET'])
def kanban_projects():
    """
    Display the projects in a Kanban board view.
    """
    show_all = request.args.get('show_all', '0') == '1'

    # Get the projects data (using existing method)
    if show_all:
        projects = get_projects()
    else:
        projects = get_projects(salesperson_id=current_user.get_salesperson_id())

    # Format dates and values for display
    for project in projects:
        if project['next_stage_deadline']:
            try:
                from datetime import datetime
                deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                project['next_stage_deadline_formatted'] = project['next_stage_deadline']
        else:
            project['next_stage_deadline_formatted'] = None

        project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
            'estimated_value'] else None

    customers = get_customers()
    salespeople = get_salespeople()
    statuses = get_project_statuses()

    return render_template('kanban.html',
                           projects=projects,
                           customers=customers,
                           salespeople=salespeople,
                           statuses=statuses,
                           show_all=show_all)


@projects_bp.route('/api/projects', methods=['GET'])
def api_list_projects():
    """
    API endpoint to get projects as JSON, with optional filtering.
    """
    try:
        # Get filter parameters
        customer_id = request.args.get('customer_id', '')
        salesperson_id = request.args.get('salesperson_id', '')
        status_id = request.args.get('status_id', '')

        # Apply filters only if they are provided
        projects = get_projects(
            customer_id=customer_id if customer_id else None,
            salesperson_id=salesperson_id if salesperson_id else None,
            status_id=status_id if status_id else None
        )

        # Format dates and values
        for project in projects:
            if project['next_stage_deadline']:
                try:
                    from datetime import datetime
                    deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                    project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    project['next_stage_deadline_formatted'] = project['next_stage_deadline']
            else:
                project['next_stage_deadline_formatted'] = None

            project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
                'estimated_value'] else None

        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/update_status', methods=['POST'])
def update_project_status(project_id):
    """
    Update a project's status (for drag and drop functionality).
    """
    try:
        status_id = request.form.get('status_id')
        if not status_id:
            return jsonify({'success': False, 'error': 'Status ID is required'})

        # Get current project data
        project = get_project_by_id(project_id)

        # Update just the status
        update_project(
            project_id=project_id,
            customer_id=project['customer_id'],
            salesperson_id=project['salesperson_id'],
            name=project['name'],
            description=project['description'],
            status_id=status_id
        )

        # Add an update comment about the status change
        statuses = get_project_statuses()
        status_name = next((s['status'] for s in statuses if s['id'] == int(status_id)), 'Unknown')

        insert_project_update(
            project_id=project_id,
            salesperson_id=current_user.get_salesperson_id(),
            comment=f"Status changed to: {status_name}"
        )

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/rfqs', methods=['GET'])
def get_project_rfqs(project_id):
    """
    Get all RFQs associated with a project as JSON.
    """
    try:
        # Import the function we defined for getting RFQs by project
        from models import get_rfqs_for_project

        rfqs = get_rfqs_for_project(project_id)
        # Ensure all objects are serializable
        for rfq in rfqs:
            for key, value in rfq.items():
                # Convert non-serializable objects to strings
                if not isinstance(value, (str, int, float, bool, type(None), list, dict)):
                    rfq[key] = str(value)

        return jsonify({'success': True, 'rfqs': rfqs})
    except Exception as e:
        print(f"Error in get_project_rfqs: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
