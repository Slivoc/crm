import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import db_cursor, execute as db_execute
from models import get_all_templates, get_template_by_id, get_all_placeholders, get_all_template_tags


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


templates_bp = Blueprint('templates', __name__)


@templates_bp.route('/templates')
def list_templates():
    templates = get_all_templates()
    return render_template('templates_list.html', templates=templates)


@templates_bp.route('/templates/create', methods=['GET', 'POST'])
def create_template():
    if request.method == 'POST':
        templates_payload = (
            request.form['name'],
            request.form['subject'],
            request.form['body'],
            request.form['description']
        )

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                INSERT INTO email_templates (name, subject, body, description)
                VALUES (?, ?, ?, ?)
                RETURNING id
            ''', templates_payload)
            template_row = cur.fetchone()
            template_id = template_row['id'] if template_row else None

            industry_tags = request.form.getlist('industry_tags')
            for tag_id in industry_tags:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO template_industry_tags (template_id, industry_tag_id)
                    VALUES (?, ?)
                    ''',
                    (template_id, tag_id)
                )

        flash('Template created successfully!', 'success')
        return redirect(url_for('templates.list_templates'))

    placeholders = get_all_placeholders()
    industry_tags = get_all_template_tags()
    return render_template(
        'templates_edit.html',
        template=None,
        placeholders=placeholders,
        industry_tags=industry_tags
    )


@templates_bp.route('/templates/edit/<int:template_id>', methods=['GET', 'POST'])
def edit_template(template_id):
    if request.method == 'POST':
        template_payload = (
            request.form['name'],
            request.form['subject'],
            request.form['body'],
            request.form['description'],
            template_id
        )

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                UPDATE email_templates 
                SET name = ?, subject = ?, body = ?, description = ?
                WHERE id = ?
            ''', template_payload)

            _execute_with_cursor(cur, 'DELETE FROM template_industry_tags WHERE template_id = ?', (template_id,))
            industry_tags = request.form.getlist('industry_tags')
            for tag_id in industry_tags:
                _execute_with_cursor(
                    cur,
                    '''
                    INSERT INTO template_industry_tags (template_id, industry_tag_id)
                    VALUES (?, ?)
                    ''',
                    (template_id, tag_id)
                )

        flash('Template updated successfully!', 'success')
        return redirect(url_for('templates.list_templates'))

    template = get_template_by_id(template_id)
    if not template:
        flash('Template not found!', 'error')
        return redirect(url_for('templates.list_templates'))

    placeholders = get_all_placeholders()
    industry_tags = get_all_template_tags()
    return render_template(
        'templates_edit.html',
        template=template,
        placeholders=placeholders,
        industry_tags=industry_tags
    )


@templates_bp.route('/templates/delete/<int:template_id>', methods=['POST'])
def delete_template(template_id):
    db_execute('DELETE FROM email_templates WHERE id = ?', (template_id,), commit=True)
    flash('Template deleted successfully!', 'success')
    return redirect(url_for('templates.list_templates'))
