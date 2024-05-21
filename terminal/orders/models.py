import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from api.mixins import ModelDiffMixin


class OrderStatus(models.Model):
    '''
        Terminal                                                    Signedup
    (id=1, title='Cоздан')                                       ------------
    (id=2, title='Опубликован')                             ('0', 'Открыто'), ('1', 'Есть отклики'),
    (id=3, title='Передан исполнителю')                         ('2', 'Выполняется'),
    (id=4, title='Выполнен')                                    ('3', 'Выполнено'),
    (id=5, title='Не выполнено')                                ('4', 'Не выполнено'),
    (id=6, title='Отменено')                                    ('5', 'Отменено'),
    (id=7, title='Закреплен за исполнителем')                   ('7', title='Закреплен за исполнителем')
    '''
    title = models.CharField('Название статуса заказа', max_length=255)

    class Meta:
        verbose_name = 'Статус заказа'
        verbose_name_plural = 'Статусы заказов'

    def __str__(self):
        return '%s' % self.title


class OrderStatusLog(models.Model):
    status = models.ForeignKey('orders.OrderStatus', verbose_name='Статус', on_delete=models.CASCADE)
    order = models.ForeignKey('orders.Order', verbose_name='Заказ', on_delete=models.CASCADE)
    created = models.DateTimeField('Дата создания', default=timezone.now)

    class Meta:
        verbose_name = 'Лог Статуса заказа'
        verbose_name_plural = 'Логи Статусов заказов'

    @property
    def get_title(self):
        if self.status_id == 5:
            return 'Не выполнен'
        if self.status_id == 6:
            return 'Отменен'
        return self.status.title

    def serialize(self):
        return {'status': self.get_title, 'created': self.created}


class OrderInvoice(models.Model):
    title = models.CharField('Название услуги', max_length=255, null=True, blank=True)
    count = models.FloatField('Количество', null=True, blank=True)
    cost = models.DecimalField('Цена за услугу для заказчика', max_digits=12, decimal_places=2, null=True, blank=True)
    cost_signedup = models.DecimalField('Цена за услугу для signedup', max_digits=12, decimal_places=2, null=True, blank=True)
    cost_contractor = models.DecimalField('Цена за услугу для подрядчика', max_digits=12, decimal_places=2, null=True, blank=True)
    service = models.ForeignKey('services.Service', verbose_name='Услуга', on_delete=models.CASCADE)
    order = models.ForeignKey('orders.Order', verbose_name='Заказ', on_delete=models.CASCADE, null=True)
    department = models.ForeignKey('clients.ClientStoreDepartment', verbose_name='Отдел Торговой точки', on_delete=models.CASCADE, null=True)
    contractor = models.ForeignKey('api.Contractor', verbose_name='Подрядчик', on_delete=models.SET_NULL, null=True)

    class Meta:
        verbose_name = 'Инвойс'
        verbose_name_plural = 'Инвойс'

    def __str__(self):
        return '%s' % self.id

    def is_discount(self):
        return self.service.discount_service

    def get_cost_contractor(self, contractor_id, cost_aggregator):
        from api.models import Contractor
        sc = Contractor.objects.filter(id=contractor_id).first()
        if not sc:
            return None

        client_percents = self.department.store.client.contractors_percent
        service_cost = self.service.cost
        if client_percents and service_cost:
            return service_cost * (float(client_percents) / 100)
        return cost_aggregator * (sc.price_percents / 100)
    
    def get_cost_aggregator(self):
        from services.models import ServiceCostContractor
        sc = ServiceCostContractor.objects.filter(service=self.service.primary_service, contractor__is_aggregator=True).first()
        return sc.cost

    def get_cost(self, services, x):
        from services.models import ServiceDiscount
        absolute_discounts = ServiceDiscount.objects.filter(service__in=services, discount_type_id=2)
        for sd in absolute_discounts:
            x += sd.value
        relative_discounts = ServiceDiscount.objects.filter(service__in=services, discount_type_id=1).order_by(
            'service_id')
        x = float(x)
        for sd in relative_discounts:
            x *= float(sd.value)
        x = round(x - 0.001, 2)
        return x


class Feedback(models.Model):
    uid = models.CharField('UID', max_length=255)
    completed = models.BooleanField('Завершен', default=False)
    adequacy = models.FloatField('Оценка за Качество / Адекватность', blank=True, null=True)
    decency = models.FloatField('Оценка за Вежливость', blank=True, null=True)
    punctuality = models.FloatField('Оценка за Пунктуальность', blank=True, null=True)
    text = models.TextField('Отзыв', blank=True, null=True)
    completed_date = models.DateTimeField('Дата заполнения', null=True, blank=True)
    order = models.ForeignKey('orders.Order', verbose_name='Заказ', on_delete=models.CASCADE, null=True)
    agreement_link = models.TextField(blank=True, null=True)
    executor_fio = models.TextField('Исполнитель ФИО', blank=True, null=True)
    executor_id = models.IntegerField('Исполнитель ID', blank=True, null=True)

    class Meta:
        verbose_name = 'Отзыв о заказе'
        verbose_name_plural = 'Отзывы о заказах'

    def __str__(self):
        return self.uid

    @property
    def rate(self):
        return round((self.adequacy + self.decency + self.punctuality) / 3, 1)

    def save(self, *args, **kwargs):
        if not self.pk:
            self.uid = secrets.token_hex(4)
        return super().save(*args, **kwargs)


class FeedbackImage(models.Model):
    feedback = models.ForeignKey(Feedback, verbose_name='Отзыв клиента', on_delete=models.CASCADE)
    image = models.FileField(upload_to='feedback_images', blank=True, null=True)
    image_link = models.TextField(blank=True, null=True)
    from_executor = models.BooleanField(default=False)

    def __str__(self):
        return str(self.id)

    @property
    def image_url(self):
        if self.image:
            return self.image.url.split('?')[0]
        return self.image_link.split('?')[0]


class Order(models.Model, ModelDiffMixin):
    phone = models.CharField('Телефон', max_length=255)
    signedup_order_text = models.TextField('Текст заказа', blank=True)
    status = models.ForeignKey(OrderStatus, verbose_name='Статус заказа', on_delete=models.CASCADE)
    store = models.ForeignKey('clients.ClientStore', verbose_name='Торговая точка', on_delete=models.CASCADE, null=True)
    department = models.ForeignKey('clients.ClientStoreDepartment', verbose_name='Отдел Торговой точки', on_delete=models.CASCADE, null=True, related_name='order_department')
    departments = models.ManyToManyField('clients.ClientStoreDepartment', verbose_name='Отделы Торговой точки', blank=True)
    customer = models.ForeignKey('orders.Customer', verbose_name='Покупатель', on_delete=models.CASCADE, null=True)
    completed_time = models.DateTimeField('Дата завершения', null=True, blank=True)
    feedback_requested = models.BooleanField('Отзыв запрошен', default=False)
    send_sms = models.BooleanField('СМС отправлен', default=False)
    data_sent = models.TextField('Данные, отправленные в сайнап', blank=True)
    cost = models.DecimalField('Цена заказа', max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'

    def __str__(self):
        return '%s' % self.id

    def price(self, invoices=None, signedup=False, contractor=False, aggregator_id=None):
        from services.models import ServiceDiscount
        if not invoices:
            invoices = OrderInvoice.objects.filter(order=self).select_related('service')
        services = [oi.service for oi in invoices]
        # Суммируем все primary * count привязанные к этому order.
        if signedup:
            if invoices[0].contractor_id is not None:
                x = sum([invoice.count * float(invoice.cost_signedup) for invoice in invoices if invoice.service.service_type_id == 1 and invoice.cost_signedup])
            else:
               x = sum([invoice.count * float(invoice.cost) for invoice in invoices if invoice.service.service_type_id == 1])
        elif contractor:
            x = sum([invoice.count * float(invoice.cost_contractor) for invoice in invoices if invoice.service.service_type_id == 1 and invoice.cost_contractor and invoice.contractor_id != aggregator_id])
        else:
            x = sum([invoice.count * float(invoice.cost) for invoice in invoices if invoice.service.service_type_id == 1])
        # Прибавляем все discount типа absolute. Запоминаем это число как X.
        absolute_discounts = ServiceDiscount.objects.filter(service__in=services, discount_type_id=2)
        for sd in absolute_discounts:
            x += sd.value
        # И полученную сумму (`X`) умножаем на discount типа relative. Запоминаем это значение как Y.
        # Если есть 2-й discount типа relative, проводим такую же оперцию, но за основу берем число Y. Очередность применения discount типа relative считаем по их services_service_id.
        relative_discounts = ServiceDiscount.objects.filter(service__in=services, discount_type_id=1).order_by('service_id')
        x = float(x)
        for sd in relative_discounts:
            x *= float(sd.value)
        x = round(x - 0.001, 2)
        return x

    def full_price(self, invoices):
        price = sum([invoice.count*float(invoice.cost) for invoice in invoices if invoice.order_id == self.id])
        return price

    @property
    def eval_fields(self):
        fields = eval(self.signedup_order_text.replace('Decimal', ''))
        return fields['fields']

    @property
    def subcategory_titles(self):
        services = [oi.service for oi in OrderInvoice.objects.filter(order=self)]
        titles = ''
        for service in services:
            for subcategory in service.subcategories:
                titles += subcategory.su_title + '*****'
        return titles[:-5]

    @property
    def draft(self):
        return OrderDraft.objects.filter(order=self).first()

    @property
    def publish(self):
        return OrderPublish.objects.filter(order=self).first()

    @property
    def date(self):
        return self.draft.created if self.draft else '?'

    @property
    def fio(self):
        return self.draft.employee.full_name if self.draft else '?'

    @property
    def publish_date(self):
        if self.publish:
            return self.publish.created
        return None

    @property
    def publish_fio(self):
        if self.publish:
            return self.publish.employee.full_name
        return None

    @property
    def feedback_obj(self):
        f = Feedback.objects.filter(order=self).first()
        return f

    @property
    def feedback(self):
        f = Feedback.objects.filter(order=self).first()
        try:
            if f:
                feedback_rate = round((f.adequacy + f.decency + f.punctuality) / 3, 1)
                return {'feedback': feedback_rate}
        except:
            pass
        return {'feedback': None}

    @property
    def feedback_rate(self):
        f = Feedback.objects.filter(order=self).first()
        try:
            return round((f.adequacy + f.decency + f.punctuality) / 3, 1)
        except:
            return None

    @property
    def dates(self):
        fields = eval(self.signedup_order_text.replace('Decimal', ''))
        return fields.get('dates', '')

    def get_signedup_order_text(self, dates=None):
        services = [oi.service.serialize(oi) for oi in OrderInvoice.objects.filter(order=self).select_related('service')]
        fields = [field_value.custom_field.serialize(field_value) for field_value in OrderCustomFieldValue.objects.filter(order=self).order_by('custom_field__index_number')]
        data = {
            'services': services,
            'fields': fields,
        }
        if dates:
            data['dates'] = dates
        return data

    @property
    def date_find_executor(self):
        d = ''
        l = OrderStatusLog.objects.filter(order=self, status_id=3).first()
        if l:
            return l.created.strftime('%Y-%m-%d %H:%M')
        return d

    @property
    def date_completed(self):
        d = ''
        l = OrderStatusLog.objects.filter(order=self, status_id=4).first()
        if l:
            return timezone.localtime(l.created).strftime('%Y-%m-%d %H:%M')
        return d

    def signedup_order_text_for_xls(self, invoice=None):
        if invoice:
            text = invoice.title
        else:
            text = ''
            invoices = OrderInvoice.objects.filter(order=self)
            for invoice in invoices:
                c = invoice.count
                if c == int(c):
                    c = int(c)
                else:
                    c = round(float(invoice.count), 1)
                text += '%s %s %s\n' % (invoice.title, c, invoice.service.unit_name)
        return text

    @property
    def deparments_dict(self):
        departments = []
        for d in self.departments.all():
            departments.append({'id': d.id, 'title': d.title})
        return departments
    
    @property
    def client_title(self):
        return self.store.client.title

    @property
    def executor_id(self):
        f = self.feedback_obj
        if f:
            return f.executor_id
        return None

    def serialize(self, is_admin=False, feedbacks=None):
        dd = self.deparments_dict
        departments_str = ', '.join([d['title'] for d in dd])
        data = {'id': self.id, 'date': self.date, 'fio': self.fio, 'published': True if self.publish else False, 'send_sms': self.send_sms}
        data.update({'status': self.status.title, 'departments': dd, 'departments_str': departments_str})
        data.update(self.feedback)

        if feedbacks:
            f = next(iter([i for i in feedbacks if i.order_id == self.id]), None)
        else:
            f = self.feedback_obj

        documents_uploaded = True if f and f.agreement_link else False
        data.update({'documents_uploaded': documents_uploaded})
        store = self.store
        data['client'] = store.client.title
        data['store_title'] = store.title
        data['city_title'] = store.city.title
        data['date_completed'] = self.date_completed

        from aidu.models import ScheduleTask
        data['dates'] = [st.serialize() for st in ScheduleTask.objects.filter(task_id=self.id)]

        data['executor_fio'] = ''
        if f:
            data['executor_fio'] = f.executor_fio
            data['executor_id'] = f.executor_id
        return data

    def save(self, need_send_sms=False, *args, **kwargs):
        if self.id and self.phone and 'phone' in self.changed_fields and need_send_sms:
            if self.send_sms:
                from api.utils import send_sms
                send_sms(self.phone, 'Ваша заявка №%s оформлена' % self.id)
        return super().save(*args, **kwargs)


class OrderDraft(models.Model):
    order = models.ForeignKey(Order, verbose_name='Заказ', on_delete=models.CASCADE)
    employee = models.ForeignKey('users.User', verbose_name='Сотрудник', on_delete=models.CASCADE)
    created = models.DateTimeField('Дата создания', default=timezone.now)

    class Meta:
        verbose_name = 'Черновик заказа'
        verbose_name_plural = 'Черновики заказов'

    def __str__(self):
        return '%s' % self.id


class OrderPublish(models.Model):
    order = models.ForeignKey(Order, verbose_name='Заказ', on_delete=models.CASCADE)
    employee = models.ForeignKey('users.User', verbose_name='Сотрудник', on_delete=models.CASCADE)
    created = models.DateTimeField('Дата создания', default=timezone.now)

    class Meta:
        verbose_name = 'Опубликованный заказ'
        verbose_name_plural = 'Опубликованные заказы'

    def __str__(self):
        return '%s' % self.id


class OrderCustomFieldTypes(models.Model):
    title = models.CharField('Название', max_length=255, default='text')

    class Meta:
        verbose_name = 'Тип кастомного поля заказа'
        verbose_name_plural = 'Типы кастомных полей заказов'

    def __str__(self):
        return '%s' % self.title


class OrderCustomFieldValue(models.Model):
    value = models.TextField('Значение')
    custom_field = models.ForeignKey('orders.OrderCustomField', verbose_name='Кастомное поле', on_delete=models.CASCADE, null=True)
    order = models.ForeignKey('orders.Order', verbose_name='Заказ', on_delete=models.CASCADE, null=True)

    class Meta:
        verbose_name = 'Значение кастомного поля заказа'
        verbose_name_plural = 'Значения кастомных полей заказов'

    def __str__(self):
        return '%s' % self.value


class OrderCustomField(models.Model):
    client = models.ForeignKey('clients.Client', verbose_name='Клиент', on_delete=models.CASCADE)
    field_type = models.ForeignKey(OrderCustomFieldTypes, verbose_name='Тип', on_delete=models.CASCADE)
    field_name = models.CharField('Название поля', max_length=255)
    label = models.CharField(max_length=255, blank=True, null=True)
    size = models.CharField('Размер', max_length=255, blank=True, null=True)
    required = models.BooleanField(default=True)
    archive = models.BooleanField('Архивный', default=False)
    index_number = models.PositiveIntegerField('Порядковый номер', default=0, editable=True)
    show_in_xls = models.BooleanField('Отображать в выгрузке для клиента', default=True)

    class Meta:
        verbose_name = 'Кастомное поле заказа'
        verbose_name_plural = 'Кастомные поля заказов'
        ordering = ['index_number']

    def __str__(self):
        return '%s' % self.field_name

    def serialize(self, field_value=None):
        d = {'id': self.id, 'index_number': self.index_number, 'field_name': self.field_name,
             'field_type': self.field_type.title, 'size': self.size, 'label':self.label, 'required': self.required}
        if field_value:
            val = field_value.value
            try:
                if type(eval(field_value.value)) == dict:
                    val = eval(field_value.value)
            except:
                pass
            d.update({'field_value': val})
        return d


class Customer(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    phone = models.CharField('Телефон', max_length=255, null=True, blank=True)
    client = models.ForeignKey('clients.Client', verbose_name='Клиент', on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'Покупатель клиента'
        verbose_name_plural = 'Покупатели клиентов'

    def __str__(self):
        return '%s' % self.uuid

    @property
    def orders(self):
        return Order.objects.filter(customer=self)


# fixme: переделать бы под использование для любых файлов вообще
class OrderXLS(models.Model):
    ids = models.TextField(null=True, blank=True)
    file_type = models.CharField(max_length=255, null=True, blank=True)
    file = models.FileField(upload_to='orders_xls', null=True, blank=True, max_length=500)

    @property
    def file_url(self):
        if self.file:
            return self.file.url.split('?')[0]
        return ''
