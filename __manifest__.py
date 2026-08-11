{
    'name': 'TruCalc Orders',
    'version': '1.0',
    'author': 'TruCalc',
    'license': 'LGPL-3',
    'category': 'Services',
    'summary': 'Evaluation Order Management',
    'depends': [
        'base',
        'mail',
        'portal',
    ],
    'data': [
        'security/trucalc_security.xml',
        'security/ir.model.access.csv',
        'data/order_sequence.xml',
        'views/evaluation_order_views.xml',
        'views/vendor_views.xml',
        'views/document_views.xml',
        'views/bid_views.xml',
    ],
    'installable': True,
    'application': True,
}