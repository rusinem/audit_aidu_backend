import datetime
import math
import os
import requests
import traceback

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Q

from pytils.dt import ru_strftime
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import status, serializers, permissions
from rest_framework.decorators import permission_classes
from xlsxwriter.workbook import Workbook

from api.models import Contractor
from clients.models import *
from orders.models import *
from services.models import *
from api.models import Log
from api.utils import str_to_bool, phone_format, send_sms as send_sms_process
from aidu.views.views_schedule import check_available_slots


class ImageSerializer(serializers.Serializer):
    image = serializers.FileField(required=True)


def user_has_access_to_order(user, order):
    """ Юзер должен быть прикреплен к отделу (для КМ - 0, СМ - 1, АО - 2) или магазину (для АМ - 3, АСМ - 4) """
    if user.is_terminal_man:
        return True
    user_departments = user.departments
    for d in order.departments.all():
        if d in user_departments:
            return True
    return False


def user_has_access_to_store(user, store_id, user_store_id=None, user_stores=None):
    if user.is_terminal_man:
        return True
    if user.is_acm or user.is_com:
        if user_stores:
            return store_id in user_stores
        else:
            store = ClientStore.objects.filter(id=store_id).first()
            return store in user.stores
    else:
        if user_store_id:
            return str(store_id) == str(user_store_id)
        return str(store_id) == str(user.store.id)


# СПИСОК ЗАКАЗОВ ДЛЯ АДМИНИСТРАТОРА ТЕРМИНАЛА
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def orders_view_admin(request):
    if not request.user.is_terminal_man:
        return Response(status=status.HTTP_403_FORBIDDEN)

    page = request.query_params.get('page')
    page = int(page) if page else 1

    order_status = request.query_params.get('status')
    order_status = order_status.split(',')
    order_sort = request.query_params.get('sort')
    order_by = '-id' if order_sort == 'desc' else 'id'
    country = request.query_params.get('country')  # ru/kz

    orders = Order.objects.filter(status_id__in=order_status)
    if country == 'kz':
        orders = orders.filter(store__city__country_title='Казахстан')
    else:
        orders = orders.exclude(store__city__country_title='Казахстан')

    orders_data = orders.prefetch_related('departments').select_related('status').select_related('store').order_by(order_by).distinct()

    p = Paginator(orders_data, 50)
    try:
        page_now = p.page(page)
        objects = page_now.object_list
    except:
        objects = []
    feedbacks = Feedback.objects.filter(order__in=objects)
    orders_list = []
    addresses = OrderCustomFieldValue.objects.filter(custom_field__field_name='address', order__in=objects)
    for o in objects:
        order = o.serialize(is_admin=True, feedbacks=feedbacks)
        address = ''
        for a in addresses:
            if a.order_id == o.id:
                address = a.value
                break
        order['address'] = address
        orders_list.append(order)
    data = {
        'current_page': page,
        'total_pages': p.num_pages,
        'orders': orders_list
    }
    return Response(data, status=status.HTTP_200_OK)


# СПИСОК ЗАКАЗОВ
@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def orders_view(request):
    if request.method == 'GET':
        stores_id = request.query_params.get('stores_id').split(',')
        store_id = None
        if len(stores_id) == 1:
            store_id = stores_id[0]
        if store_id:
            if not user_has_access_to_store(request.user, store_id):
                return Response(status=status.HTTP_403_FORBIDDEN)

        published = str_to_bool(request.query_params.get('published')) if 'published' in request.query_params else False

        page = request.query_params.get('page')
        page = int(page) if page else None
        count = request.query_params.get('count')
        count = int(count) if count else 50
        successful = str_to_bool(request.query_params.get('successful'))

        user = request.user
        user_departments_id = [d.id for d in user.departments]

        orders = Order.objects.filter(store_id__in=stores_id, departments__id__in=user_departments_id)
        if published:
            if successful:
                orders = orders.filter(status_id__in=[2, 3, 4, 7])
            else:
                orders = orders.filter(status_id__in=[5, 6])
        else:
            orders = orders.filter(status_id=1)
        orders = orders.prefetch_related('departments').select_related('status').select_related('store').order_by('-id').distinct()

        p = Paginator(orders, count)
        try:
            page_now = p.page(page)
            objects = page_now.object_list
        except:
            objects = []
        feedbacks = Feedback.objects.filter(order__in=objects)

        orders_list = []
        addresses = OrderCustomFieldValue.objects.filter(custom_field__field_name='address', order__in=objects)
        for o in objects:
            order = o.serialize(is_admin=False, feedbacks=feedbacks)
            address = ''
            for a in addresses:
                if a.order_id == o.id:
                    address = a.value
                    break
            order['address'] = address
            orders_list.append(order)

        data = {
            'current_page': page,
            'total_pages': p.num_pages,
            'orders': orders_list
        }
        return Response(data, status=status.HTTP_200_OK)

    if request.method == 'POST':
        user = request.user
        if user.is_consult:
            return Response(status=status.HTTP_403_FORBIDDEN)

        o = Order.objects.get(id=request.data.get('order_id'))
        if not user_has_access_to_order(user, o):
            return Response(status=status.HTTP_403_FORBIDDEN)
        if not user.can_publish_orders:
            return Response(status=status.HTTP_403_FORBIDDEN)
        if o.store.is_archive:
            return Response({'error': 'Магазин удален'}, status=status.HTTP_400_BAD_REQUEST)
        return send_order_to_signedup(o, user)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def orders_delete(request):
    if request.method == 'POST':
        # проверяем, что юзер имеет доступ
        orders_id = request.GET.get('orders_id')
        orders = Order.objects.filter(
            id__in=[int(i) for i in orders_id.split(',')])
        for order in orders:
            if not user_has_access_to_order(request.user, order):
                pass
            else:
                order.delete()
        return Response(status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def orders_xls2(request):
    if request.method == 'GET':
        user = request.user

        if request.user.is_terminal_coworker:
            return Response(status=status.HTTP_403_FORBIDDEN)

        orders_id = request.GET.get('orders_id', '')
        departments_id = request.GET.get('departments_id', '')
        store_id = request.GET.get('store_id', '')
        client_id = request.GET.get('client_id', '')
        date_to = request.GET.get('date_to')
        date_from = request.GET.get('date_from')
        contractor_id = request.GET.get('contractor_id')

        is_client_price = str_to_bool(request.GET.get('is_client_price'))
        is_aggregator_price = str_to_bool(
            request.GET.get('is_aggregator_price'))
        is_executor_fio = str_to_bool(request.GET.get('is_executor_fio'))

        is_contractor_name = str_to_bool(request.GET.get('is_contractor_name'))
        is_contractor_price = str_to_bool(
            request.GET.get('is_contractor_price'))

        is_concatenate_services = str_to_bool(request.query_params.get(
            'is_concatenate_services')) if 'is_concatenate_services' in request.query_params else True

        orders = Order.objects.filter(status_id=4)
        if orders_id:
            orders = orders.filter(id__in=[int(i)
                                   for i in orders_id.split(',')])
        if departments_id:
            orders = orders.filter(departments__id__in=[
                                   int(i) for i in departments_id.split(',')])
        if store_id:
            orders = orders.filter(
                store_id__in=[int(i) for i in store_id.split(',')])
        if client_id:
            orders = orders.filter(store__client_id__in=[
                                   int(i) for i in client_id.split(',')])
        if date_to:
            orders = orders.filter(completed_time__date__lte=date_to)
        if date_from:
            orders = orders.filter(completed_time__date__gte=date_from)
        if not user.is_terminal_man:
            orders = orders.filter(store__in=user.stores)
        orders = orders.select_related('status').select_related(
            'store').prefetch_related('departments').order_by('id').distinct()

        custom_fields_headers = []
        if contractor_id:
            orders_ids = [oi.order_id for oi in OrderInvoice.objects.filter(
                Q(contractor_id=contractor_id) | Q(contractor_id=None))]
            orders = [o for o in orders if o.id in orders_ids]
        else:
            c_id = None
            if client_id:
                c_id = client_id
            else:
                if len(orders) > 0:
                    c_id = orders[0].store.client_id
            if c_id:
                custom_fields = OrderCustomField.objects.filter(
                    client_id=c_id, show_in_xls=True).order_by('-index_number')
                for cf in custom_fields:
                    custom_fields_headers.append(
                        {'id': cf.id, 'title': cf.label})

        random_hash = secrets.token_hex(4)
        fname = 'orders_%s_%s.xlsx' % (str(datetime.date.today()), random_hash)
        os.makedirs("orders_xls", exist_ok=True)
        if user.is_terminal_man:
            fn = 'orders_xls/%s/%s' % (user.id, fname)
            os.makedirs("orders_xls/%s" % user.id, exist_ok=True)
        else:
            fn = 'orders_xls/%s/%s' % (user.client.id, fname)
            os.makedirs("orders_xls/%s" % user.client.id, exist_ok=True)
        workbook = Workbook(fn)
        worksheet = workbook.add_worksheet()
        bold_left = workbook.add_format({'bold': True, 'align': 'left'})

        columns = ['ID заказа', 'Город', 'Торговая точка', 'Отдел',
                   'Сотрудник, опубликовавший заказ', 'Перечень услуг в заказе']
        if is_client_price:
            columns += ['Суммарная стоимость услуг по цене для клиента']
        if is_aggregator_price:
            columns += ['Суммарная стоимость услуг по цене для AIDU']

        if is_contractor_name:
            columns += ['Имя подрядчика']
        if is_contractor_price:
            columns += ['Суммарная стоимость услуг по цене для подрядчика']

        if len(custom_fields_headers) > 0:
            for cf in custom_fields_headers:
                columns += [cf['title']]

        columns += ['Отзыв']
        if is_executor_fio:
            columns += ['Исполнитель']
        columns += [
            'Статус заказа',  'Дата создания заказа',
            'Дата публикации заказа', 'Нашли исполнителя', 'Закрыли заказ']

        for i, val in enumerate(columns):
            worksheet.write(0, i, val, bold_left)

        data = []

        invoices = OrderInvoice.objects.filter(order__in=orders).select_related(
            'contractor').select_related('service')
        aggregator = Contractor.objects.filter(is_aggregator=True).first()
        aggregator_id = aggregator.id

        if is_concatenate_services:
            for order in orders:
                order_invoices = [
                    i for i in invoices if i.order_id == order.id]
                contractors = list(
                    set([i.contractor.title for i in order_invoices if i.contractor]))
                if len(contractors) == 0:
                    contractors = [aggregator.title]
                contractor_title = ', '.join(contractors)
                cost_contractor = order.price(
                    invoices=order_invoices, contractor=True, aggregator_id=aggregator_id)
                departments = ', '.join(
                    [d.title for d in order.departments.all()])
                f = order.feedback_obj
                feedback_rate = ''
                executor_fio = ''
                if f:
                    try:
                        feedback_rate = round(
                            (f.adequacy + f.decency + f.punctuality) / 3, 1)
                    except:
                        pass
                    executor_fio = f.executor_fio

                order_data = [
                    order.id, order.store.city.title, order.store.title, departments,
                    order.publish_fio, order.signedup_order_text_for_xls(),
                ]
                if is_client_price:
                    price = float(order.cost)
                    order_data += [price]
                if is_aggregator_price:
                    cost_signedup = order.price(
                        invoices=order_invoices, signedup=True, aggregator_id=aggregator_id)
                    order_data += [cost_signedup]

                if is_contractor_name:
                    order_data += [contractor_title]
                if is_contractor_price:
                    order_data += [round(cost_contractor + 0.01)]

                if len(custom_fields_headers) > 0:
                    for cf in custom_fields_headers:
                        cf_value = OrderCustomFieldValue.objects.filter(
                            order_id=order.id, custom_field_id=cf['id']).first()
                        if cf_value:
                            order_data += [cf_value.value]
                        else:
                            order_data += ['']

                order_data += [feedback_rate]
                if is_executor_fio:
                    order_data += [executor_fio]

                order_data += [
                    order.status.title, order.date.strftime('%Y-%m-%d %H:%M'),
                    order.publish_date.strftime(
                        '%Y-%m-%d %H:%M'), order.date_find_executor, order.date_completed
                ]
                data.append(order_data)

        else:
            for order in orders:
                order_invoices = [
                    i for i in invoices if i.order_id == order.id]
                services = [oi.service for oi in order_invoices]

                contractors = list(
                    set([i.contractor.title for i in order_invoices if i.contractor]))
                if len(contractors) == 0:
                    contractors = [aggregator.title]
                contractor_title = ', '.join(contractors)
                for order_invoice in order_invoices:
                    if order_invoice.contractor_id == aggregator.id:
                        cost_contractor = 0
                    else:
                        cost_contractor = round(float(order_invoice.get_cost(
                            services, order_invoice.cost_contractor)) + 0.01) if order_invoice.cost_contractor else ''
                    departments = order_invoice.department.title
                    f = order.feedback_obj
                    feedback_rate = ''
                    executor_fio = ''
                    if f:
                        try:
                            feedback_rate = round(
                                (f.adequacy + f.decency + f.punctuality) / 3, 1)
                        except:
                            pass
                        executor_fio = f.executor_fio

                    order_data = [
                        order.id, order.store.city.title, order.store.title, departments,
                        order.publish_fio, order.signedup_order_text_for_xls(
                            invoice=order_invoice),
                    ]
                    if is_client_price:
                        price = float(order_invoice.get_cost(
                            services, order_invoice.cost)) if order_invoice.cost else ''
                        order_data += [price]
                    if is_aggregator_price:
                        cost_signedup = float(order_invoice.get_cost(
                            services, order_invoice.cost_signedup)) if order_invoice.cost_signedup else ''
                        order_data += [cost_signedup]

                    if is_contractor_name:
                        order_data += [contractor_title]
                    if is_contractor_price:
                        order_data += [cost_contractor]

                    if len(custom_fields_headers) > 0:
                        for cf in custom_fields_headers:
                            cf_value = OrderCustomFieldValue.objects.filter(order_id=order.id,
                                                                            custom_field_id=cf['id']).first()
                            if cf_value:
                                order_data += [cf_value.value]
                            else:
                                order_data += ['']

                    order_data += [feedback_rate]
                    if is_executor_fio:
                        order_data += [executor_fio]

                    order_data += [
                        order.status.title, order.date.strftime(
                            '%Y-%m-%d %H:%M'),
                        order.publish_date.strftime(
                            '%Y-%m-%d %H:%M'), order.date_find_executor, order.date_completed
                    ]
                    for i in range(int(order_invoice.count)):
                        data.append(order_data)

        for row_num, row_data in enumerate(data):
            for col_num, col_data in enumerate(row_data):
                worksheet.write(row_num+1, col_num, col_data)
        workbook.close()

        with open(fn, "rb") as fh:
            with ContentFile(fh.read()) as file_content:
                xls = OrderXLS.objects.create(ids=orders_id, file_type='orders_xls')
                xls.file.save(fname, file_content)
                xls.save()
        return Response({'link': xls.file_url}, status=status.HTTP_200_OK)


def services_struct(all_services_objects, user, order=None, invoices=None):
    department_data = {}
    services_by_department = []
    for service, service_serialize in all_services_objects:
        if order and order.publish:
            dep = ClientStoreDepartment.objects.get(
                id=service_serialize.get('oi_department_id'))
            if dep in department_data:
                department_data[dep].append(service_serialize)
            else:
                department_data[dep] = [service_serialize]
        else:
            if user.is_terminal_man:
                depts = []
                for d in service.store.departments():
                    depts.append(d)
            else:
                if user.is_am or user.is_acm:
                    depts = []
                    for d in service.store.departments():
                        depts.append(d)
                else:
                    depts = service.departments.filter(employees__in=[user])

            for dep in depts:
                if not order:
                    if dep in service.departments.all():
                        if dep in department_data:
                            department_data[dep].append(service_serialize)
                        else:
                            department_data[dep] = [service_serialize]
                else:
                    if service_serialize.get('oi_department_id') == dep.id:
                        service_serialize.pop('oi_id')
                        service_serialize.pop('oi_department_id')
                        if dep in department_data:
                            department_data[dep].append(service_serialize)
                        else:
                            department_data[dep] = [service_serialize]

    for dep, services in department_data.items():
        services = [k for j, k in enumerate(
            services) if k not in services[j + 1:]]
        services.sort(key=lambda item: item.get('discount_type') or '')
        d = {
            'department_title': dep.title,
            'department_id': dep.id,
            'department_services': services
        }
        services_by_department.append(d)
    return services_by_department


# КАРТОЧКА ЗАКАЗА
@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def orders_new(request):
    if request.method == 'GET':
        user = request.user
        if not user_has_access_to_store(request.user, request.query_params.get('store_id')):
            return Response(status=status.HTTP_403_FORBIDDEN)

        if request.user.is_acm or request.user.is_com:
            store = ClientStore.objects.filter(id=request.query_params.get('store_id')).first()
        else:
            store = user.store

        department_id = request.query_params.get('department_id')
        if department_id:
            try:
                department = ClientStoreDepartment.objects.get(id=department_id)
            except:
                return Response({'error': 'Отдел не найден'}, status=status.HTTP_400_BAD_REQUEST)
            if department not in user.departments:
                return Response(status=status.HTTP_403_FORBIDDEN)
            services = department.services()
        else:
            services = store.services(user=request.user)

        services_primary = []
        services_discount = []
        all_services_objects = []
        for service in services:
            service_serialize = service.serialize()
            if service.service_type_id == 1:
                services_primary.append(service_serialize)
            if service.service_type_id == 2:
                services_discount.append(service_serialize)
            all_services_objects.append([service, service_serialize])

        fields = [f.serialize() for f in OrderCustomField.objects.filter(client=store.client, archive=False).select_related('field_type')]
        # 1. показываем все услуги для данного магазина (Service)
        # 2. показываем все поля для заполнения в заказе (OrderCustomField)
        data = {
            'services': services_struct(all_services_objects, user),
            'fields': fields,
            'city_id': store.city.city_id
        }
        return Response(data, status=status.HTTP_200_OK)

    if request.method == 'POST':
        user = request.user
        if not user_has_access_to_store(request.user, request.data.get('store_id')):
            return Response(status=status.HTTP_403_FORBIDDEN)

        if request.user.is_acm or request.user.is_com:
            store = ClientStore.objects.filter(id=request.data.get('store_id')).first()
        else:
            store = user.store
        if store.is_archive:
            return Response({'error': 'Магазин удален'}, status=status.HTTP_400_BAD_REQUEST)

        # 0. Customer, Order
        # 1. Service >>> OrderInvoice
        # 2. OrderCustomField >>> OrderCustomFieldValue
        # 3. Draft

        dates = request.data.get('dates')
        if not dates:
            return Response({'error': 'Не выбрана дата'}, status=status.HTTP_400_BAD_REQUEST)
        dates_clean = []
        for date in dates.split(','):
            day = datetime.datetime.strptime(date, "%Y-%m-%d")
            if day.date() < datetime.datetime.now().date() + datetime.timedelta(days=60):
                dates_clean.append(date)

        services = []
        services_data = eval(request.data['services'])
        for service in services_data:
            service_id = service.get('id')
            service = Service.objects.get(id=service_id)
            services.append(service)

        if len(dates_clean) > 0 and not check_available_slots(dates_clean, services, store.city.city_id):
            ru_dates = []
            for date in dates.split(','):
                day = ru_strftime('%d %B %Y', inflected=True, date=datetime.datetime.strptime(date, "%Y-%m-%d"))
                ru_dates.append(day)
            return Response({'error': 'Нет свободных исполнителей на эти даты: %s' % ', '.join(ru_dates)}, status=status.HTTP_400_BAD_REQUEST)
        
        phone = request.data.get('phone')
        send_sms = str_to_bool(request.data.get('send_sms'))
        customer, _ = Customer.objects.get_or_create(phone=phone_format(phone), client=user.client)
        o = Order.objects.create(phone=phone, status_id=1, send_sms=send_sms, store=store, customer=customer)
        OrderStatusLog.objects.create(order=o, status_id=1)

        for service in services_data:
            service_id = service.get('id')
            department_id = service.get('department_id')
            count = service.get('count')
            service = Service.objects.get(id=service_id)
            if service.service_type_id == 1:
                cost = service.cost
                cost_signedup = service.cost_signedup
            else:
                cost = None
                cost_signedup = None
            OrderInvoice.objects.create(count=count, service=service, order=o, cost=cost, title=service.title,
                                        department_id=department_id, cost_signedup=cost_signedup)
            o.departments.add(int(department_id))

        fields = eval(request.data['fields'])
        for field in fields:
            custom_field_id = field.get('id')
            value = field.get('value')
            OrderCustomFieldValue.objects.create(value=value, custom_field_id=custom_field_id, order=o)
        OrderDraft.objects.create(order=o, employee=user)

        o.signedup_order_text = o.get_signedup_order_text(dates=dates)
        o.cost = o.price()
        o.save()
        if o.send_sms:
            send_sms_process(o.phone, 'Ваша заявка №%s. Служба поддержки https://vk.cc/cqDT0M' % o.id)
        return Response({'id': o.id}, status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def order(request, order_id):
    if request.method == 'GET':
        user = request.user

        o = Order.objects.get(id=order_id)
        if not user.is_terminal_man:
            if not user_has_access_to_order(user, o):
                return Response(status=status.HTTP_403_FORBIDDEN)
        if not user.is_terminal_man:
            if not user_has_access_to_store(request.user, o.store_id):
                return Response(status=status.HTTP_403_FORBIDDEN)

        # 1. показываем все услуги для данного заказа (Service-OrderInvoice)
        # 2. показываем все поля для заполнения в заказе (OrderCustomField-OrderCustomFieldValue)

        services_primary = []
        services_discount = []
        invoices = OrderInvoice.objects.filter(
            order=o).select_related('service')
        all_services_objects = []
        for oi in invoices:
            service = oi.service
            service_serialize = service.serialize(oi)
            service_serialize['title'] = oi.title
            service_serialize['oi_id'] = oi.id
            service_serialize['oi_department_id'] = oi.department_id
            if service.service_type_id == 1:
                services_primary.append(service_serialize)
            if service.service_type_id == 2:
                services_discount.append(service_serialize)
            all_services_objects.append([service, service_serialize])
        fields = [field_value.custom_field.serialize(field_value) for field_value in OrderCustomFieldValue.objects.filter(
            order=o).order_by('custom_field__index_number')]

        # 1. показываем все услуги для данного магазина (Service)
        # 2. показываем все поля для заполнения в заказе (OrderCustomField)
        # 3. информацию по Заказу
        departments = []
        client = o.store.client
        for d in o.departments.all():
            departments.append({'id': d.id, 'title': d.title})
        order_data = {'id': o.id, 'date': o.date, 'fio': o.fio, 'publish_fio': o.publish_fio, 'status': o.status.title,
                      'phone': o.phone, 'departments': departments, 'send_sms': o.send_sms, 'cost': float(o.cost) if o.cost else None,
                      'client_id': client.id, 'client_title': client.title}
        logs = OrderStatusLog.objects.filter(order=o)
        order_data.update({'logs': [l.serialize() for l in logs]})

        data = {
            'services': services_struct(all_services_objects, user, order=o, invoices=invoices),
            'fields': fields,
            'order': order_data,
            'own_blank': client.own_blank,
            'dates': o.dates,
            'executor_id': o.executor_id
        }
        return Response(data, status=status.HTTP_200_OK)

    if request.method == 'POST':
        user = request.user
        if not user_has_access_to_store(request.user, request.data.get('store_id')):
            return Response(status=status.HTTP_403_FORBIDDEN)

        o = Order.objects.get(id=order_id)
        if not user_has_access_to_order(user, o):
            return Response(status=status.HTTP_403_FORBIDDEN)

        if str_to_bool(request.data.get('published')):
            if not user.can_publish_orders:
                return Response(status=status.HTTP_403_FORBIDDEN)

        if o.store.is_archive:
            return Response({'error': 'Магазин удален'}, status=status.HTTP_400_BAD_REQUEST)
        # редактирование заказа
        # публикация заказа

        dates = request.data.get('dates')
        # TODOoooooooo
        # if not dates:
        #     return Response({'error': 'Не выбрана дата'}, status=status.HTTP_400_BAD_REQUEST)

        phone = request.data.get('phone')
        o.phone = phone
        o.save(need_send_sms=True)

        services_data = eval(request.data['services'])

        current_ids = [(int(service.get('id')), int(service.get('department_id')))
                       for service in services_data if service.get('id')]
        for old_oi in OrderInvoice.objects.filter(order=o):
            if (old_oi.service_id, old_oi.department_id) not in current_ids:
                old_oi.delete()
        # current_ids = [int(service.get('id')) for service in services_data if service.get('id')]
        # OrderInvoice.objects.filter(order=o).exclude(service_id__in=current_ids).delete()

        for service in services_data:
            service_id = service.get('id')
            count = service.get('count')
            department_id = service.get('department_id')
            service = Service.objects.get(id=service_id)
            if service.service_type_id == 1:
                cost = service.cost
                cost_signedup = service.cost_signedup
            else:
                cost = None
                cost_signedup = None

            oi = OrderInvoice.objects.filter(
                service_id=service_id, order=o, department_id=department_id).first()
            if not oi:
                OrderInvoice.objects.create(count=count, service=service, order=o, cost=cost, title=service.title,
                                            department_id=department_id, cost_signedup=cost_signedup)
            else:
                OrderInvoice.objects.filter(service_id=service_id, order=o, department_id=department_id).update(
                    count=count, cost=cost, cost_signedup=cost_signedup)

            o.departments.add(int(department_id))

        fields = eval(request.data['fields'])
        current_ids = [int(field.get('id'))
                       for field in fields if field.get('id')]
        OrderCustomFieldValue.objects.filter(order=o).exclude(
            custom_field_id__in=current_ids).delete()
        for field in fields:
            custom_field_id = field.get('id')
            value = field.get('value')
            cf = OrderCustomFieldValue.objects.filter(
                custom_field_id=custom_field_id, order=o).first()
            if not cf:
                OrderCustomFieldValue.objects.create(
                    value=value, custom_field_id=custom_field_id, order=o)
            else:
                OrderCustomFieldValue.objects.filter(
                    custom_field_id=custom_field_id, order=o).update(value=value)

        o.signedup_order_text = o.get_signedup_order_text(dates=dates)
        o.cost = o.price()
        o.save()

        if str_to_bool(request.data.get('published')):
            return send_order_to_signedup(o, user)
        else:
            OrderDraft.objects.create(order=o, employee=user)
            return Response({'id': o.id}, status=status.HTTP_200_OK)


@api_view(['GET', 'POST'])
def feedback(request, order_id):
    if request.method == 'GET':
        try:
            cf = Feedback.objects.get(order_id=order_id)
        except Feedback.DoesNotExist:
            return Response({'error': 'Object does not exist'}, status=status.HTTP_400_BAD_REQUEST)

        fio = OrderCustomFieldValue.objects.filter(
            order=cf.order).filter(custom_field__field_name='fio').first()
        log = OrderStatusLog.objects.filter(
            order=cf.order, status_id=4).first()
        departments = []
        for d in cf.order.departments.all():
            departments.append({'id': d.id, 'title': d.title})
        data = {
            'customer': {
                'date': cf.completed_date,
                'adequacy': cf.adequacy,
                'decency': cf.decency,
                'punctuality': cf.punctuality,
                'text': cf.text,
                'departments': departments,
                'images': [i.image_url for i in FeedbackImage.objects.filter(feedback=cf, from_executor=False)],
                'fio': fio.value if fio else ''
            },
            'executor': {
                'agreement_link': cf.agreement_link.split('?')[0],
                'images': [i.image_url for i in FeedbackImage.objects.filter(feedback=cf, from_executor=True)],
                'fio': cf.executor_fio,
                'date': log.created if log else ''
            }
        }
        return Response(data, status=status.HTTP_200_OK)

    if request.method == 'POST':
        try:
            cf = Feedback.objects.get(order_id=order_id)
        except Feedback.DoesNotExist:
            return Response({'error': 'Object does not exist'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cf.text = request.data.get('text')
            cf.adequacy = request.data.get('adequacy')
            cf.decency = request.data.get('decency')
            cf.punctuality = request.data.get('punctuality')
            cf.completed = True
            cf.completed_date = datetime.datetime.now()
            cf.save()
        except:
            Log.objects.create(category='orders', function='feedback', title='feedback_id=%s' % cf.id, text=traceback.format_exc())
            return Response({'success': False}, status=status.HTTP_200_OK)

        for image in request.FILES:
            data = {'image': request.FILES.get(image)}
            serializer = ImageSerializer(
                data=data, context={'request': request})
            if serializer.is_valid():
                img = serializer.validated_data.get('image')
                FeedbackImage.objects.create(image=img, feedback=cf)
            else:
                Log.objects.create(category='orders', function='feedback image %s' %
                                   cf.id, title=serializer.errors, text=request.data)

        return Response({'success': True}, status=status.HTTP_200_OK)


def send_order_to_signedup(o, user):
    client = user.client

    client_percents = o.store.client.contractors_percent
    if client_percents:
        client_percents = float(client_percents)

    data = {
        'client_percents': client_percents,
        'signedup_order_text': o.signedup_order_text,
        'signedup_account_api_key': settings.TERMINAL_API_KEY,
        'title': 'Корпоративный заказ от %s' % o.store.client.title,
        'subcategory_titles': o.subcategory_titles,
        'phone': o.phone,
        'client_name': client.title if client else None,
        'client_logo': client.get_logo_content() if client else '',
        'client_type': client.client_type if client else '',
        'terminal_id': o.id,
        'city_id': o.store.city.city_id,
        'customer': o.customer.id
    }
    if o.store.contractor_id:
        data['prefered_contractor_id'] = o.store.contractor_id
    r = requests.post(settings.SIGNEDUP_API_SITE + 'api/tasks/task/', data=data)
    if r.status_code == 201:
        o.signedup_task_id = r.json().get('id')
        o.status_id = 2
        o.data_sent = data
        o.save()
        OrderPublish.objects.create(order=o, employee=user)
        OrderStatusLog.objects.create(order=o, status_id=2)
        return Response({'id': o.id}, status=status.HTTP_200_OK)
    else:
        Log.objects.create(
            category='orders', function='order to signedup', title=r.text, text=data)
        if r.json().get('already_exist'):
            o.status_id = 2
            o.data_sent = data
            o.save()
            OrderPublish.objects.create(order=o, employee=user)
            OrderStatusLog.objects.create(order=o, status_id=2)
            return Response({'id': o.id}, status=status.HTTP_200_OK)
        return Response({'error': 'signed up error'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def orders_search(request):
    ''' ПОИСК ЗАКАЗОВ ПО ВСЕМ ПОЛЯМ '''
    if not (request.user.is_coworker or request.user.is_terminal_man):
        return Response(status=status.HTTP_403_FORBIDDEN)

    search = request.query_params.get('search')
    c = request.query_params.get('c', 1)

    orders_id = [op.order_id for op in list(
        OrderPublish.objects.all().select_related('order')) * c]
    orders = Order.objects.filter(
        id__in=orders_id).prefetch_related('departments')

    data = []

    for order in orders:
        if request.user.is_terminal_man:
            has_access = True
        else:
            has_access = user_has_access_to_order(request.user, order)

        if has_access:
            if search in order.phone:
                data.append(order.serialize())
                continue

            fields = order.eval_fields
            for v in fields:
                if search == v.get('field_value') or search in v.get('field_value'):
                    data.append(order.serialize())
                    break

    return Response(data, status=status.HTTP_200_OK)


def change_status_in_signedup(order: Order, status_id: int) -> Response:
    """ Запрос в сайнап, где меняем статус Спец.Заказа. В случае успеха - меням статус и в Терминале """
    try:
        data = {
            'signedup_account_api_key': settings.TERMINAL_API_KEY,
            'order_id': order.id,
            'status_id': status_id
        }
        r = requests.post(settings.SIGNEDUP_API_SITE +
                          'api/specialtasks/status/', data=data)
        if r.status_code != 200:
            try:
                error = r.json()['error']
            except:
                error = r.text
            return Response({'error': error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        old_status = order.status_id
        if old_status not in [5, 6]:
            order.status_id = status_id
            order.save()
            OrderStatusLog.objects.create(order=order, status_id=status_id)
        if status_id in [5, 6]:
            # Статус выполнения заказа, если он был в логах - удаляем.
            OrderStatusLog.objects.filter(order=order, status_id=4).delete()
        return Response(status=status.HTTP_200_OK)
    except:
        Log.objects.create(category='orders', function='change_status_in_signedup',
                           title='order_id=%s, status_id=%s' % (order.id, status_id), text=traceback.format_exc())
        return Response({'error': 'Ошибка. Обратитесь к Администратору'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def user_can_change_status(user, order_id: int, terminal_admin_only=False) -> Order or Response:
    user = user
    try:
        order = Order.objects.get(id=order_id)
    except:
        return None, {'text': 'Заказ не найден', 'status': status.HTTP_404_NOT_FOUND}
    if terminal_admin_only:
        if not user.is_terminal_man:
            return None, {'text': 'Роль не подходит', 'status': status.HTTP_403_FORBIDDEN}
    else:
        if user.is_consult:
            return None, {'text': 'Роль не подходит', 'status': status.HTTP_403_FORBIDDEN}
        if not user_has_access_to_order(user, order):
            return None, {'text': 'Нет доступа к заказу', 'status': status.HTTP_403_FORBIDDEN}
    return order, None


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def order_cancel(request, order_id):
    """ Все роли, кроме КМ могут менять статус заказа на "Отменено" """
    order, error = user_can_change_status(request.user, order_id)
    if not order:
        return Response({'error': error['text']}, status=error['status'])
    return change_status_in_signedup(order, 6)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def order_fail(request, order_id):
    """ Все роли, кроме КМ могут менять статус заказа на "Не выполнено" """
    order, error = user_can_change_status(request.user, order_id)
    if not order:
        return Response({'error': error['text']}, status=error['status'])
    return change_status_in_signedup(order, 5)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def executor_assign(request):
    order_id = request.data.get('order_id')
    executor_phone = request.data.get('executor_phone')
    order, error = user_can_change_status(request.user, order_id, terminal_admin_only=True)
    if not order:
        return Response({'error': error['text']}, status=error['status'])

    # Запрос в сайнап, где меняем Исполнителя Спец.Заказа
    try:
        data = {
            'signedup_account_api_key': settings.TERMINAL_API_KEY,
            'order_id': order.id,
            'executor_phone': executor_phone
        }
        r = requests.post(settings.SIGNEDUP_API_SITE + 'api/specialtasks/executor-assign/', data=data)
        if r.status_code != 200:
            try:
                error = r.json()['error']
            except:
                error = r.text
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

        # Если у заказа не было исполнителя - исполнитель назначается, меняется статус заказа
        if order.status_id == 2:
            order.status_id = 3
            order.save()
            OrderStatusLog.objects.create(order=order, status_id=3)

        cf, created = Feedback.objects.get_or_create(order_id=order.id)
        if created:
            cf.executor_fio = r.json().get('executor_fio')
            cf.executor_id = r.json().get('executor_id')
        else:
            if str(cf.executor_id) != str(r.json().get('executor_id')):
                cf.executor_fio = r.json().get('executor_fio')
                cf.executor_id = r.json().get('executor_id')
        cf.save()

        return Response(status=status.HTTP_200_OK)
    except:
        Log.objects.create(category='orders', function='executor_assign',
                           title='order_id=%s, executor_phone=%s' % (order.id, executor_phone), text=traceback.format_exc())
        return Response({'error': 'Ошибка. Обратитесь к Администратору'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

